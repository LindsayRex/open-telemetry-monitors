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
from opentelemetry.metrics import Observation

# Import our configuration and collectors
from lib.config import (
    logger, resource, OTEL_METRICS_ENDPOINT, OTEL_LOGS_ENDPOINT, OTEL_TRACES_ENDPOINT,
    COLLECTION_INTERVAL_SECONDS, LOG_COLLECTION_INTERVAL_SECONDS, 
    ENABLE_TRACES
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
    
    # Setup OTLP HTTP exporter for traces - only if enabled
    if ENABLE_TRACES:
        trace_exporter = OTLPSpanExporter(endpoint=OTEL_TRACES_ENDPOINT)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tracer_provider)
        tracer = trace.get_tracer("proxmox.kernel")
        logger.info("Trace exporting enabled")
    else:
        # Set up a no-op tracer that doesn't actually send data
        tracer_provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(tracer_provider)
        tracer = trace.get_tracer("proxmox.kernel")
        logger.info("Trace exporting disabled - using no-op tracer")
    
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
        'cpu_usage': meter.create_gauge(
            name="proxmox_cpu_usage_percent",
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
    
    # Dedicated ZFS metric callbacks for each metric
    def zfs_pool_health_status_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            health_value = metrics.get('health_value', 0)
            health_text = str(metrics.get('health', 'UNKNOWN'))
            logger.info(f"Yielding zfs_pool_health_status: {health_value}, {{pool: {pool}, health_text: {health_text}}}")
            logger.debug(f"Yielding zfs_pool_health_status Observation: value={health_value}, labels={{'pool': {pool}, 'health_text': {health_text}}}")
            yield Observation(health_value, {"pool": pool, "health_text": health_text})

    def zfs_pool_capacity_ratio_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            capacity = metrics.get('capacity', 0.0)
            logger.info(f"Yielding zfs_pool_capacity_ratio: {capacity}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_capacity_ratio Observation: value={capacity}, labels={{'pool': {pool}}}")
            yield Observation(capacity, {"pool": pool})

    def zfs_pool_fragmentation_ratio_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            fragmentation = metrics.get('fragmentation', 0.0)
            logger.info(f"Yielding zfs_pool_fragmentation_ratio: {fragmentation}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_fragmentation_ratio Observation: value={fragmentation}, labels={{'pool': {pool}}}")
            yield Observation(fragmentation, {"pool": pool})

    def zfs_pool_checksum_errors_total_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            checksum_errors = metrics.get('checksum_errors', 0)
            logger.info(f"Yielding zfs_pool_checksum_errors_total: {checksum_errors}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_checksum_errors_total Observation: value={checksum_errors}, labels={{'pool': {pool}}}")
            yield Observation(checksum_errors, {"pool": pool})

    def zfs_pool_read_bytes_total_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            read_bytes = metrics.get('read_bytes', 0)
            logger.info(f"Yielding zfs_pool_read_bytes_total: {read_bytes}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_read_bytes_total Observation: value={read_bytes}, labels={{'pool': {pool}}}")
            yield Observation(read_bytes, {"pool": pool})

    def zfs_pool_write_bytes_total_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            write_bytes = metrics.get('write_bytes', 0)
            logger.info(f"Yielding zfs_pool_write_bytes_total: {write_bytes}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_write_bytes_total Observation: value={write_bytes}, labels={{'pool': {pool}}}")
            yield Observation(write_bytes, {"pool": pool})

    def zfs_pool_read_ops_total_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            read_ops = metrics.get('read_ops', 0)
            logger.info(f"Yielding zfs_pool_read_ops_total: {read_ops}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_read_ops_total Observation: value={read_ops}, labels={{'pool': {pool}}}")
            yield Observation(read_ops, {"pool": pool})

    def zfs_pool_write_ops_total_callback(options):
        for pool, metrics in collect_zfs_pool_metrics().items():
            write_ops = metrics.get('write_ops', 0)
            logger.info(f"Yielding zfs_pool_write_ops_total: {write_ops}, {{pool: {pool}}}")
            logger.debug(f"Yielding zfs_pool_write_ops_total Observation: value={write_ops}, labels={{'pool': {pool}}}")
            yield Observation(write_ops, {"pool": pool})

    # Dedicated disk I/O metric callbacks for each metric
    def proxmox_disk_io_read_bytes_total_callback(options):
        for device, metrics in collect_disk_io_data_raw().items():
            logger.info(f"Yielding proxmox_disk_io_read_bytes_total: {metrics['bytes_read']}, {{device: {device}}}")
            logger.debug(f"Yielding proxmox_disk_io_read_bytes_total Observation: value={metrics['bytes_read']}, labels={{'device': {device}}}")
            yield Observation(metrics['bytes_read'], {"device": device})

    def proxmox_disk_io_write_bytes_total_callback(options):
        for device, metrics in collect_disk_io_data_raw().items():
            logger.info(f"Yielding proxmox_disk_io_write_bytes_total: {metrics['bytes_written']}, {{device: {device}}}")
            logger.debug(f"Yielding proxmox_disk_io_write_bytes_total Observation: value={metrics['bytes_written']}, labels={{'device': {device}}}")
            yield Observation(metrics['bytes_written'], {"device": device})

    # Register each ZFS and disk I/O metric with its own callback
    created_instruments['zfs_pool_health_status'] = meter.create_observable_gauge(
        name="zfs_pool_health_status",
        description="ZFS pool health status (0=ONLINE, 1=DEGRADED, 2=FAULTED, 3=OFFLINE, 4=UNAVAIL, 5=REMOVED)",
        callbacks=[zfs_pool_health_status_callback],
        unit="state"
    )
    created_instruments['zfs_pool_capacity_ratio'] = meter.create_observable_gauge(
        name="zfs_pool_capacity_ratio",
        description="ZFS pool capacity usage percentage",
        callbacks=[zfs_pool_capacity_ratio_callback],
        unit="%"
    )
    created_instruments['zfs_pool_fragmentation_ratio'] = meter.create_observable_gauge(
        name="zfs_pool_fragmentation_ratio",
        description="ZFS pool fragmentation percentage",
        callbacks=[zfs_pool_fragmentation_ratio_callback],
        unit="%"
    )
    created_instruments['zfs_pool_checksum_errors_total'] = meter.create_observable_counter(
        name="zfs_pool_checksum_errors_total",
        description="Total ZFS pool checksum errors - use increase() or rate() in queries",
        callbacks=[zfs_pool_checksum_errors_total_callback],
        unit="errors"
    )
    created_instruments['zfs_pool_read_bytes_total'] = meter.create_observable_counter(
        name="zfs_pool_read_bytes_total",
        description="Total bytes read from ZFS pool - use rate() in queries",
        callbacks=[zfs_pool_read_bytes_total_callback],
        unit="bytes"
    )
    created_instruments['zfs_pool_write_bytes_total'] = meter.create_observable_counter(
        name="zfs_pool_write_bytes_total",
        description="Total bytes written to ZFS pool - use rate() in queries",
        callbacks=[zfs_pool_write_bytes_total_callback],
        unit="bytes"
    )
    created_instruments['zfs_pool_read_ops_total'] = meter.create_observable_counter(
        name="zfs_pool_read_ops_total",
        description="Total read operations on ZFS pool - use rate() in queries",
        callbacks=[zfs_pool_read_ops_total_callback],
        unit="operations"
    )
    created_instruments['zfs_pool_write_ops_total'] = meter.create_observable_counter(
        name="zfs_pool_write_ops_total",
        description="Total write operations on ZFS pool - use rate() in queries",
        callbacks=[zfs_pool_write_ops_total_callback],
        unit="operations"
    )
    created_instruments['proxmox_disk_io_read_bytes_total'] = meter.create_observable_counter(
        name="proxmox_disk_io_read_bytes_total",
        description="Total bytes read from disk - use rate() in queries",
        callbacks=[proxmox_disk_io_read_bytes_total_callback],
        unit="bytes"
    )
    created_instruments['proxmox_disk_io_write_bytes_total'] = meter.create_observable_counter(
        name="proxmox_disk_io_write_bytes_total",
        description="Total bytes written to disk - use rate() in queries",
        callbacks=[proxmox_disk_io_write_bytes_total_callback],
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
                
                logger.info(f"Metrics collected and sent to {OTEL_METRICS_ENDPOINT}")
                if ENABLE_TRACES:
                    logger.info(f"Traces sent to {OTEL_TRACES_ENDPOINT}")
            
            # Wait for the next collection interval
            time.sleep(COLLECTION_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)  # Wait a bit before retrying

if __name__ == "__main__":
    main()