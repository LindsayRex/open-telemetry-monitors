#!/usr/bin/env python3
"""
Log collectors for Proxmox OpenTelemetry Monitoring
"""
import json
import os
import time
from datetime import datetime
from pygtail import Pygtail

from lib.config import (
   logger, LOG_FILES, JOURNAL_SERVICES, last_journal_timestamp
)
from lib.utils import run_command, create_log_record

def collect_and_send_logs(logger_otel):
    """Collect system logs from Proxmox and send them via OpenTelemetry."""
    logger.info("Collecting system logs")
    
    for log_file in LOG_FILES:
        if not os.path.exists(log_file):
            continue
            
        # Determine log severity based on the log file name
        if "error" in log_file.lower():
            severity = "ERROR"
        elif "warn" in log_file.lower():
            severity = "WARN"
        elif "debug" in log_file.lower():
            severity = "DEBUG"
        else:
            severity = "INFO"
            
        # Extract log source from the filename
        log_source = os.path.basename(log_file).replace('.log', '')
        
        try:
            # Use pygtail to read only new lines since last run
            for line in Pygtail(log_file, every_n=100):
                line = line.strip()
                if not line:  # Skip empty lines
                    continue
                    
                # Create and emit a proper log record
                if logger_otel:
                    log_record = create_log_record(
                        timestamp=int(time.time() * 1e9),
                        body=line,
                        severity=severity,
                        attributes={
                            "log.file": log_file,
                            "log.source": log_source
                        }
                    )
                    logger_otel.emit(log_record)
        except Exception as e:
            logger.error(f"Error processing log file {log_file}: {e}")

def collect_and_send_journal_logs(logger_otel):
    """Collect systemd journal logs for specified services and send them via OpenTelemetry."""
    global last_journal_timestamp
    
    logger.info("Collecting journal logs")
    
    # Build a journalctl command that gets logs since our last check
    # Convert microseconds to seconds and format as ISO timestamp
    since_time = datetime.fromtimestamp(last_journal_timestamp / 1000000).strftime('%Y-%m-%d %H:%M:%S')
    
    # Create a filter for the services we want to monitor
    services_filter = ""
    if JOURNAL_SERVICES:
        services_filter = " ".join([f"-u {service}" for service in JOURNAL_SERVICES])
    
    # Run journalctl with output format that matches syslog
    # We use the short-precise format which includes timestamp, hostname, service name, and pid
    journal_cmd = f"journalctl -S '{since_time}' --no-pager -o short-precise {services_filter}"
    
    journal_output = run_command(journal_cmd)
    
    if journal_output:
        # Process each line from journalctl
        for line in journal_output.strip().split('\n'):
            if not line:
                continue
                
            try:
                # Parse the syslog-formatted line
                # Example format: May 07 22:05:18 pmox vsce-sign[130821]: Primary signature status: OK
                
                # Skip lines that contain CPU/system monitoring data
                if any(x in line.lower() for x in ["throttled to", "frequency", "cpu core"]):
                    continue
                    
                # Create and emit the log record
                if logger_otel:
                    log_record = create_log_record(
                        timestamp=int(time.time() * 1e9),  # Current time in nanoseconds
                        body=line,  # Use the full syslog-formatted line from journalctl
                        severity="INFO",  # Default severity
                        attributes={
                            "log.source": "journal",
                            "format": "syslog"
                        }
                    )
                    logger_otel.emit(log_record)
                
            except Exception as e:
                logger.error(f"Error processing journal entry: {e}")
    
    # Update the timestamp so we don't get stuck
    last_journal_timestamp = int(datetime.now().timestamp() * 1000000)