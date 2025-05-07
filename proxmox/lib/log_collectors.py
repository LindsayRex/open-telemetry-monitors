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
    services_filter = " ".join([f"_SYSTEMD_UNIT={service}" for service in JOURNAL_SERVICES])
    
    # Run journalctl with JSON output for easier parsing
    journal_cmd = f"journalctl -S '{since_time}' -o json"
    if services_filter:
        journal_cmd += f" {services_filter}"
    
    journal_output = run_command(journal_cmd)
    
    if journal_output:
        # journalctl with -o json outputs one JSON object per line
        for line in journal_output.strip().split('\n'):
            if not line:
                continue
                
            try:
                entry = json.loads(line)
                
                # Extract the timestamp (in microseconds)
                timestamp = int(entry.get('__REALTIME_TIMESTAMP', '0'))
                if timestamp > last_journal_timestamp:
                    last_journal_timestamp = timestamp
                
                # Convert to nanoseconds for OpenTelemetry
                otel_timestamp = timestamp * 1000
                
                # Extract severity
                priority = int(entry.get('PRIORITY', '6'))
                if priority <= 3:
                    severity = "ERROR"
                elif priority == 4:
                    severity = "WARN"
                elif priority == 5 or priority == 6:
                    severity = "INFO"
                else:
                    severity = "DEBUG"
                
                # Extract the service name
                service_name = entry.get('_SYSTEMD_UNIT', 'unknown')
                
                # Get the log message
                message = entry.get('MESSAGE', '')
                
                # Create and emit the log record
                if logger_otel:
                    log_record = create_log_record(
                        timestamp=otel_timestamp,
                        body=message,
                        severity=severity,
                        attributes={
                            "log.source": "journal",
                            "service.name": service_name,
                            "hostname": entry.get('_HOSTNAME', ''),
                            "pid": entry.get('_PID', ''),
                            "syslog_identifier": entry.get('SYSLOG_IDENTIFIER', '')
                        }
                    )
                    logger_otel.emit(log_record)
                
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Error parsing journal entry: {e}")
    
    # Always update the timestamp so we don't get stuck
    if last_journal_timestamp == 0:
        last_journal_timestamp = int(datetime.now().timestamp() * 1000000)