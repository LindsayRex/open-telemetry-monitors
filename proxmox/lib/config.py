#!/usr/bin/env python3
"""
Configuration settings for Proxmox OpenTelemetry Monitoring
"""
import glob
import os
import logging
from datetime import datetime
from opentelemetry.sdk.resources import Resource

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='/var/log/proxmox-otel.log'
)
logger = logging.getLogger("proxmox-otel")

# OpenTelemetry server configuration
OTEL_METRICS_ENDPOINT = "http://192.168.0.185:4318/v1/metrics"
OTEL_LOGS_ENDPOINT = "http://192.168.0.185:4318/v1/logs"
OTEL_TRACES_ENDPOINT = "http://192.168.0.185:4318/v1/traces"  # Endpoint for Tempo tracing
COLLECTION_INTERVAL_SECONDS = 30  # How often to collect and send metrics
LOG_COLLECTION_INTERVAL_SECONDS = 60  # How often to collect and send logs

# Proxmox log files to monitor - Reduced list to focus on critical logs
LOG_FILES = [
    "/var/log/syslog",
    "/var/log/kern.log",
    "/var/log/auth.log"
]

# Add only critical Proxmox logs
PVE_LOGS = glob.glob("/var/log/pve/cluster*.log")  # Only cluster-related logs
LOG_FILES.extend(PVE_LOGS)

# Systemd journal services to monitor
JOURNAL_SERVICES = [
    "pvedaemon.service",
    "pvestatd.service", 
    "qemu-server.service",
    "systemd-journald.service",
    "sshd.service",
    "pvescheduler.service"
]

# Time to remember the last journal timestamp we processed
last_journal_timestamp = datetime.now().timestamp() * 1000000  # microseconds

# Resource attributes
resource = Resource.create(
    {
        "service.name": "proxmox-server",
        "service.namespace": "infrastructure",
        "host.name": os.uname().nodename,
    }
)

# Alert thresholds
TEMP_CRITICAL_THRESHOLD = 5  # Degrees below critical temperature to start alerting
DISK_TEMP_WARNING_THRESHOLD = 10  # Degrees below critical to start alerting for disks
CPU_THROTTLE_THRESHOLD = 1500  # MHz, alert if CPU frequency drops below this value