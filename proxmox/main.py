#!/usr/bin/env python3
"""
Main entry point for Proxmox OpenTelemetry Monitoring
"""
import sys
import time
import threading
import json
import re

# Add the virtual environment path
sys.path.append('/opt/proxmox-otel/venv/lib/python3.11/site-packages')
sys.path.append('/opt/proxmox-otel')

from opentelemetry import metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider, get_logger
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# Import our configuration and collectors
from lib.config import (
    logger, resource, OTEL_METRICS_ENDPOINT, OTEL_LOGS_ENDPOINT, OTEL_TRACES_ENDPOINT,
    COLLECTION_INTERVAL_SECONDS, LOG_COLLECTION_INTERVAL_SECONDS
)
from lib.utils import run_command

# Import modular collectors
from lib.collectors.system_collector import collect_system_metrics, collect_disk_io_data_raw
from lib.collectors.vm_collector import collect_vm_metrics
from lib.collectors.temperature_collector import collect_temperature_metrics
from lib.collectors.storage_collector import collect_storage_metrics, collect_disk_smart_metrics
from lib.collectors.zfs_collector import collect_zfs_pool_metrics

from lib.log_collectors import (
    collect_and_send_logs, collect_and_send_journal_logs
)

# Global dictionary to store created instruments for access in callbacks
created_instruments = {}

# Define global variables for ZFS collection tracking
zfs_last_collection_timestamp = 0
zfs_collection_lock = threading.Lock()

# Define global variables for disk I/O collection tracking
disk_io_last_collection_timestamp = 0
disk_io_collection_lock = threading.Lock()

def setup_opentelemetry():
    """Set up OpenTelemetry exporters for metrics, logs, and traces."""
    # Setup OTLP HTTP exporter for metrics
    metrics_exporter = OTLPMetricExporter(endpoint=OTEL_METRICS_ENDPOINT)
    reader = PeriodicExportingMetricReader(
        metrics_exporter,
        export_interval_millis=COLLECTION_INTERVAL_SECONDS * 1000
    )
    meter_provider = MeterProvider(metric_readers=[reader], resource=resource)
    metrics.set_meter_provider(meter_provider)
    
    # Setup OTLP HTTP exporter for logs
    log_exporter = OTLPLogExporter(endpoint=OTEL_LOGS_ENDPOINT)
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(log_provider)
    logger_otel = get_logger("proxmox.logs")
    
    # Setup OTLP HTTP exporter for traces
    trace_exporter = OTLPSpanExporter(endpoint=OTEL_TRACES_ENDPOINT)
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(tracer_provider)
    tracer = trace.get_tracer("proxmox.kernel")
    
    # Create a meter and define metrics
    meter = metrics.get_meter("proxmox.metrics")
    
    # Define metrics - store in a dictionary for easy access
    metrics_dict = {
        # Temperature metrics - enhanced for comprehensive monitoring
        'temperature': meter.create_gauge(
            name="proxmox_temperature",
            description="Temperature in degrees Celsius from various sensors",
            unit="celsius"
        ),
        
        # CPU metrics
        'cpu_frequency': meter.create_gauge(
            name="proxmox_cpu_frequency",
            description="CPU frequency in MHz",
            unit="MHz"
        ),
        'cpu_usage': meter.create_gauge(
            name="proxmox_cpu_usage",
            description="CPU usage percentage",
            unit="%"
        ),
        
        # Memory metrics
        'memory_used': meter.create_gauge(
            name="proxmox_memory_used",
            description="Memory used in bytes",
            unit="bytes"
        ),
        'memory_total': meter.create_gauge(
            name="proxmox_memory_total",
            description="Total memory in bytes",
            unit="bytes"
        ),
        'memory_usage': meter.create_gauge(
            name="proxmox_memory_usage",
            description="Memory usage percentage",
            unit="%"
        ),
        
        # Node uptime
        'node_uptime': meter.create_gauge(
            name="proxmox_node_uptime",
            description="Node uptime in seconds",
            unit="s"
        ),
        
        # Cluster metrics
        'cluster_quorate': meter.create_gauge(
            name="proxmox_cluster_quorate",
            description="Cluster quorum status (1=quorate, 0=not quorate)",
            unit="state"
        ),
        'cluster_nodes': meter.create_gauge(
            name="proxmox_cluster_node_online",
            description="Cluster node online status (1=online, 0=offline)",
            unit="state"
        ),
        
        # Storage metrics
        'storage_status': meter.create_gauge(
            name="proxmox_storage_status",
            description="Storage status (1=active, 0=inactive)",
            unit="state"
        ),
        'storage_usage': meter.create_gauge(
            name="proxmox_storage_usage",
            description="Storage usage percentage",
            unit="%"
        ),
        'storage_used': meter.create_gauge(
            name="proxmox_storage_used",
            description="Storage used in bytes",
            unit="bytes"
        ),
        'storage_total': meter.create_gauge(
            name="proxmox_storage_total",
            description="Total storage in bytes",
            unit="bytes"
        ),
        
        # SMART metrics
        'smart_metrics': meter.create_gauge(
            name="proxmox_smart_attributes",
            description="SMART disk attributes",
            unit="value"
        ),
        
        # VM metrics
        'vm_status': meter.create_gauge(
            name="proxmox_vm_status",
            description="VM status (1=running, 0=stopped)",
            unit="state"
        ),
        'vm_cpu_usage': meter.create_gauge(
            name="proxmox_vm_cpu_usage",
            description="VM CPU usage percentage",
            unit="%"
        ),
        'vm_memory_usage': meter.create_gauge(
            name="proxmox_vm_memory_usage",
            description="VM memory usage percentage",
            unit="%"
        ),
    }
    
    # Define the ZFS metrics callback function
    def zfs_metrics_callback(options):
        """Callback function for ZFS observable metrics"""
        global zfs_last_collection_timestamp
        
        # This callback is registered for each ZFS observable instrument
        # To prevent running the expensive collect_zfs_pool_metrics multiple times
        # within a single collection cycle, add time-based deduplication
        
        # Only one thread should perform collection if callbacks are concurrent
        acquired_lock = zfs_collection_lock.acquire(blocking=False)
        if not acquired_lock:
            # Another callback invocation is already handling collection
            if False:  # Make this a generator without yielding anything
                yield
            return

        try:
            now = time.monotonic()
            # Check if it's time to collect again
            if (now - zfs_last_collection_timestamp) >= COLLECTION_INTERVAL_SECONDS:
                try:
                    # Call the ZFS metrics collector passing in all the instruments from created_instruments
                    collect_zfs_pool_metrics(
                        health_status=created_instruments['zfs_pool_health_status'],
                        capacity_ratio=created_instruments['zfs_pool_capacity_ratio'],
                        frag_ratio=created_instruments['zfs_pool_fragmentation_ratio'],
                        checksum_errors=created_instruments['zfs_pool_checksum_errors_total'],
                        read_bytes=created_instruments['zfs_pool_read_bytes_total'],
                        write_bytes=created_instruments['zfs_pool_write_bytes_total'],
                        read_ops=created_instruments['zfs_pool_read_ops_total'],
                        write_ops=created_instruments['zfs_pool_write_ops_total']
                    )
                    zfs_last_collection_timestamp = now  # Update last collection time
                except Exception as e:
                    logger.error(f"Error in ZFS metrics collection: {e}")
            else:
                # Not time to collect yet, but callback was invoked
                pass  # No actual collection needed this time
                
        finally:
            zfs_collection_lock.release()  # Always release the lock
            
        # This makes the function a generator, satisfying the SDK's expectation
        if False:
            yield
    
    # Create Observable instruments for ZFS metrics and store in global dictionary
    # Observable Gauges (for point-in-time values)
    created_instruments['zfs_pool_health_status'] = meter.create_observable_gauge(
        name="zfs_pool_health_status",
        description="ZFS pool health status (0=ONLINE, 1=DEGRADED, 2=FAULTED, 3=OFFLINE, 4=UNAVAIL, 5=REMOVED)",
        callbacks=[zfs_metrics_callback],
        unit="state"
    )
    
    created_instruments['zfs_pool_capacity_ratio'] = meter.create_observable_gauge(
        name="zfs_pool_capacity_ratio",
        description="ZFS pool capacity usage percentage",
        callbacks=[zfs_metrics_callback],
        unit="%"
    )
    
    created_instruments['zfs_pool_fragmentation_ratio'] = meter.create_observable_gauge(
        name="zfs_pool_fragmentation_ratio",
        description="ZFS pool fragmentation percentage",
        callbacks=[zfs_metrics_callback],
        unit="%"
    )
    
    # Observable Counters (for cumulative values)
    created_instruments['zfs_pool_checksum_errors_total'] = meter.create_observable_counter(
        name="zfs_pool_checksum_errors_total",
        description="Total ZFS pool checksum errors - use increase() or rate() in queries",
        callbacks=[zfs_metrics_callback],
        unit="errors"
    )
    
    created_instruments['zfs_pool_read_bytes_total'] = meter.create_observable_counter(
        name="zfs_pool_read_bytes_total",
        description="Total bytes read from ZFS pool - use rate() in queries",
        callbacks=[zfs_metrics_callback],
        unit="bytes"
    )
    
    created_instruments['zfs_pool_write_bytes_total'] = meter.create_observable_counter(
        name="zfs_pool_write_bytes_total",
        description="Total bytes written to ZFS pool - use rate() in queries",
        callbacks=[zfs_metrics_callback],
        unit="bytes"
    )
    
    created_instruments['zfs_pool_read_ops_total'] = meter.create_observable_counter(
        name="zfs_pool_read_ops_total",
        description="Total read operations on ZFS pool - use rate() in queries",
        callbacks=[zfs_metrics_callback],
        unit="operations"
    )
    
    created_instruments['zfs_pool_write_ops_total'] = meter.create_observable_counter(
        name="zfs_pool_write_ops_total",
        description="Total write operations on ZFS pool - use rate() in queries",
        callbacks=[zfs_metrics_callback],
        unit="operations"
    )
    
    # Define the system disk I/O metrics callback function
    def disk_io_metrics_callback(options):
        """Callback function for disk I/O observable metrics"""
        global disk_io_last_collection_timestamp
        
        # Only one thread should perform collection if callbacks are concurrent
        acquired_lock = disk_io_collection_lock.acquire(blocking=False)
        if not acquired_lock:
            # Another callback invocation is already handling collection
            if False:  # Make this a generator without yielding anything
                yield
            return
        
        try:
            now = time.monotonic()
            # Check if it's time to collect again
            if (now - disk_io_last_collection_timestamp) >= COLLECTION_INTERVAL_SECONDS:
                try:
                    # Get the node info for labels
                    node_status = run_command("pvesh get /nodes/`hostname`/status -output-format json")
                    node_labels = {}
                    
                    if node_status:
                        try:
                            node_data = json.loads(node_status)
                            hostname = node_data.get('pveversion', 'unknown').split('/')[-1]
                            node_id = node_data.get('node', 'unknown')
                            
                            # Basic labels for all metrics
                            node_labels = {
                                "node": node_id,
                                "hostname": hostname
                            }
                        except Exception as e:
                            logger.error(f"Error parsing node data in disk I/O callback: {e}")
                    
                    # Call the raw data collector function
                    io_data = collect_disk_io_data_raw()
                    
                    # For each device, observe the metrics
                    for device, metrics in io_data.items():
                        device_labels = dict(node_labels, **{"device": device})
                        
                        # Get the instruments and call observe on them
                        read_instrument = created_instruments.get('proxmox_disk_io_read_bytes_total')
                        write_instrument = created_instruments.get('proxmox_disk_io_write_bytes_total')
                        
                        if read_instrument:
                            read_instrument.observe(metrics['bytes_read'], attributes=device_labels)
                        
                        if write_instrument:
                            write_instrument.observe(metrics['bytes_written'], attributes=device_labels)
                    
                    disk_io_last_collection_timestamp = now  # Update last collection time
                except Exception as e:
                    logger.error(f"Error in disk I/O metrics callback: {e}")
            else:
                # Not time to collect yet, but callback was invoked
                pass  # No actual collection needed this time
                
        finally:
            disk_io_collection_lock.release()  # Always release the lock
            
        # This makes the function a generator, satisfying the SDK's expectation
        if False:
            yield
    
    # Create Observable instruments for disk I/O metrics and store in global dictionary
    created_instruments['proxmox_disk_io_read_bytes_total'] = meter.create_observable_counter(
        name="proxmox_disk_io_read_bytes_total",
        description="Total bytes read from disk - use rate() in queries",
        callbacks=[disk_io_metrics_callback],
        unit="bytes"
    )
    
    created_instruments['proxmox_disk_io_write_bytes_total'] = meter.create_observable_counter(
        name="proxmox_disk_io_write_bytes_total",
        description="Total bytes written to disk - use rate() in queries",
        callbacks=[disk_io_metrics_callback],
        unit="bytes"
    )
    
    # Add all observable instruments to metrics_dict for convenience
    metrics_dict.update(created_instruments)
    
    return metrics_dict, logger_otel, tracer

def log_collection_thread(logger_otel):
    """Thread function for continuous log collection."""
    while True:
        try:
            collect_and_send_logs(logger_otel)
            collect_and_send_journal_logs(logger_otel)
            time.sleep(LOG_COLLECTION_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in log collection thread: {e}")
            time.sleep(10)  # Wait a bit before retrying

def collect_cpu_frequency_metrics(cpu_frequency_gauge, logger_otel=None):
    """Collect CPU frequency metrics."""
    logger.info("Collecting CPU frequency metrics")
    
    try:
        # Get CPU frequency scaling information
        freq_info = {}
        
        # Read CPU frequency for each core
        try:
            import glob
            import os
            
            # Find all CPU frequency files
            cpu_freq_files = glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq')
            
            for freq_file in cpu_freq_files:
                try:
                    # Extract CPU number from path
                    cpu_num = int(freq_file.split('/cpu')[1].split('/')[0])
                    
                    # Read current frequency
                    with open(freq_file, 'r') as f:
                        # Frequency is in kHz, convert to MHz
                        freq_khz = int(f.read().strip())
                        freq_mhz = freq_khz / 1000
                        
                        # Store frequency
                        freq_info[cpu_num] = freq_mhz
                        
                        # Set metric
                        if cpu_frequency_gauge:
                            cpu_frequency_gauge.set(freq_mhz, {
                                "core": str(cpu_num)
                            })
                except (ValueError, IOError) as e:
                    logger.error(f"Error reading CPU {cpu_num} frequency: {e}")
            
        except Exception as e:
            logger.error(f"Error collecting CPU frequency metrics: {e}")
        
        return freq_info
        
    except Exception as e:
        logger.error(f"Unexpected error while collecting CPU frequency metrics: {e}")
        return {}

def main():
    """Main function to run the monitoring script."""
    logger.info("Starting Proxmox OpenTelemetry Monitoring")
    
    # Set up OpenTelemetry
    metrics_dict, logger_otel, tracer = setup_opentelemetry()
    
    # Start the log collection in a separate thread
    log_thread = threading.Thread(
        target=log_collection_thread, 
        args=(logger_otel,),
        daemon=True
    )
    log_thread.start()
    
    # Main monitoring loop
    while True:
        try:
            # Create a monitoring cycle span to track overall collection process
            with tracer.start_as_current_span("monitoring_cycle") as monitoring_span:
                monitoring_span.set_attribute("collection.timestamp", time.time())
                
                # Collect system metrics
                with tracer.start_as_current_span("system_metrics_collection") as span:
                    collect_system_metrics(
                        cpu_usage=metrics_dict['cpu_usage'],
                        memory_usage=metrics_dict['memory_usage'],
                        memory_total=metrics_dict['memory_total'],
                        memory_used=metrics_dict['memory_used'],
                        node_uptime=metrics_dict['node_uptime']
                    )
                    span.set_attribute("collector.name", "system")
                
                # Collect storage metrics
                with tracer.start_as_current_span("storage_metrics_collection") as span:
                    collect_storage_metrics(
                        storage_status=metrics_dict['storage_status'],
                        storage_usage=metrics_dict['storage_usage'],
                        storage_used=metrics_dict['storage_used'],
                        storage_total=metrics_dict['storage_total']
                    )
                    span.set_attribute("collector.name", "storage")
                
                # Collect SMART disk metrics
                with tracer.start_as_current_span("smart_metrics_collection") as span:
                    collect_disk_smart_metrics(
                        smart_metrics=metrics_dict['smart_metrics']
                    )
                    span.set_attribute("collector.name", "smart")
                
                # Collect VM metrics
                with tracer.start_as_current_span("vm_metrics_collection") as span:
                    collect_vm_metrics(
                        vm_status=metrics_dict['vm_status'],
                        vm_cpu_usage=metrics_dict['vm_cpu_usage'],
                        vm_memory_usage=metrics_dict['vm_memory_usage']
                    )
                    span.set_attribute("collector.name", "vm")
                
                # Collect temperature metrics with enhanced temperature monitoring
                with tracer.start_as_current_span("temperature_metrics_collection") as span:
                    collect_temperature_metrics(
                        metrics_dict['temperature'],
                        logger_otel
                    )
                    span.set_attribute("collector.name", "temperature")
                
                # ZFS metrics are now collected via Observable instruments callbacks
                # No need to call collect_zfs_pool_metrics() here
                
                # CPU frequency metrics collection disabled
                # collect_cpu_frequency_metrics(
                #     metrics_dict['cpu_frequency'],
                #     logger_otel
                # )
                
                logger.info(f"Metrics collected and sent to {OTEL_METRICS_ENDPOINT}")
                logger.info(f"Traces sent to {OTEL_TRACES_ENDPOINT}")
            
            # Wait for the next collection interval
            time.sleep(COLLECTION_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)  # Wait a bit before retrying

if __name__ == "__main__":
    main()