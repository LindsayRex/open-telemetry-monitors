#!/usr/bin/env python3
"""
Configuration settings for Proxmox OpenTelemetry Monitoring
"""
import glob
import os
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from opentelemetry.sdk.resources import Resource

# Log file configuration
LOG_FILE_PATH = '/var/log/proxmox-otel.log'
MAX_LOG_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5

# Configure logging with rotation
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("proxmox-otel")
logger.setLevel(logging.INFO)  # Set to INFO for normal operation

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Add rotating file handler
file_handler = RotatingFileHandler(
    LOG_FILE_PATH,
    maxBytes=MAX_LOG_SIZE_BYTES,
    backupCount=BACKUP_COUNT
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Optional: Uncomment to add console logging during development
# console_handler = logging.StreamHandler()
# console_handler.setFormatter(formatter)
# logger.addHandler(console_handler)

# OpenTelemetry server configuration - Environment variables with fallbacks
OTEL_COLLECTOR_HOST = os.getenv("OTEL_COLLECTOR_HOST", "192.168.0.185")
OTEL_COLLECTOR_PORT = os.getenv("OTEL_COLLECTOR_PORT", "4318")

OTEL_METRICS_ENDPOINT = f"http://{OTEL_COLLECTOR_HOST}:{OTEL_COLLECTOR_PORT}/v1/metrics"
OTEL_LOGS_ENDPOINT = f"http://{OTEL_COLLECTOR_HOST}:{OTEL_COLLECTOR_PORT}/v1/logs"
OTEL_TRACES_ENDPOINT = f"http://{OTEL_COLLECTOR_HOST}:{OTEL_COLLECTOR_PORT}/v1/traces"  # Endpoint for Tempo tracing
COLLECTION_INTERVAL_SECONDS = int(os.getenv("OTEL_COLLECTION_INTERVAL", "30"))  # How often to collect and send metrics
LOG_COLLECTION_INTERVAL_SECONDS = int(os.getenv("OTEL_LOG_COLLECTION_INTERVAL", "60"))  # How often to collect and send logs

# Feature toggles
ENABLE_TRACES = os.getenv("ENABLE_TRACES", "false").lower() in ("true", "1", "yes")  # Disabled by default

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