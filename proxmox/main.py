#!/usr/bin/env python3
"""
Main entry point for Proxmox OpenTelemetry Monitoring
"""
import sys
import time
import threading

# Add the virtual environment path
sys.path.append('/opt/proxmox-otel/venv/lib/python3.11/site-packages')
sys.path.append('/opt/proxmox-otel')

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider, get_logger

# Import our configuration and collectors
from lib.config import (
    logger, resource, OTEL_METRICS_ENDPOINT, OTEL_LOGS_ENDPOINT,
    COLLECTION_INTERVAL_SECONDS, LOG_COLLECTION_INTERVAL_SECONDS
)

# Import modular collectors
from lib.collectors.system_collector import collect_system_metrics
from lib.collectors.vm_collector import collect_vm_metrics
from lib.collectors.temperature_collector import collect_temperature_metrics
from lib.collectors.storage_collector import collect_storage_metrics, collect_disk_smart_metrics

from lib.log_collectors import (
    collect_and_send_logs, collect_and_send_journal_logs
)

def setup_opentelemetry():
    """Set up OpenTelemetry exporters for metrics and logs."""
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
    
    # Create a meter and define metrics
    meter = metrics.get_meter("proxmox.metrics")
    
    # Define metrics
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
        
        # Disk metrics
        'disk_io_read': meter.create_gauge(
            name="proxmox_disk_io_read",
            description="Disk read bytes",
            unit="bytes"
        ),
        'disk_io_write': meter.create_gauge(
            name="proxmox_disk_io_write",
            description="Disk write bytes",
            unit="bytes"
        ),
        
        # Network metrics
        'net_in_bytes': meter.create_gauge(
            name="proxmox_net_in_bytes",
            description="Network input bytes",
            unit="bytes"
        ),
        'net_out_bytes': meter.create_gauge(
            name="proxmox_net_out_bytes",
            description="Network output bytes",
            unit="bytes"
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
        )
    }
    
    return metrics_dict, logger_otel

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
    metrics_dict, logger_otel = setup_opentelemetry()
    
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
            # Collect system metrics
            collect_system_metrics(
                cpu_usage=metrics_dict['cpu_usage'],
                memory_usage=metrics_dict['memory_usage'],
                memory_total=metrics_dict['memory_total'],
                memory_used=metrics_dict['memory_used'],
                node_uptime=metrics_dict['node_uptime'],
                disk_io_read=metrics_dict['disk_io_read'],
                disk_io_write=metrics_dict['disk_io_write'],
                net_in_bytes=metrics_dict['net_in_bytes'],
                net_out_bytes=metrics_dict['net_out_bytes']
            )
            
            # Collect storage metrics
            collect_storage_metrics(
                storage_status=metrics_dict['storage_status'],
                storage_usage=metrics_dict['storage_usage'],
                storage_used=metrics_dict['storage_used'],
                storage_total=metrics_dict['storage_total']
            )
            
            # Collect SMART disk metrics
            collect_disk_smart_metrics(
                smart_metrics=metrics_dict['smart_metrics']
            )
            
            # Collect VM metrics
            collect_vm_metrics(
                vm_status=metrics_dict['vm_status'],
                vm_cpu_usage=metrics_dict['vm_cpu_usage'],
                vm_memory_usage=metrics_dict['vm_memory_usage']
            )
            
            # Collect temperature metrics with enhanced temperature monitoring
            collect_temperature_metrics(
                metrics_dict['temperature'],
                logger_otel
            )
            
            # Collect CPU frequency metrics
            collect_cpu_frequency_metrics(
                metrics_dict['cpu_frequency'],
                logger_otel
            )
            
            logger.info(f"Metrics collected and sent to {OTEL_METRICS_ENDPOINT}")
            
            # Wait for the next collection interval
            time.sleep(COLLECTION_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)  # Wait a bit before retrying

if __name__ == "__main__":
    main()