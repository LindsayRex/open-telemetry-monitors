#!/usr/bin/env python3
"""
Utility functions for Proxmox OpenTelemetry Monitoring
"""
import subprocess
from opentelemetry._logs import SeverityNumber  # Import SeverityNumber from the API
from opentelemetry.sdk._logs import LogRecord
from opentelemetry.trace import TraceFlags
from opentelemetry.trace.span import INVALID_SPAN_ID, INVALID_TRACE_ID

from lib.config import logger, resource

def run_command(command):
    """Run a shell command and return the output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Command '{command}' failed: {e}")
        logger.error(f"Command stderr: {e.stderr}")
        return None

def create_log_record(timestamp, body, severity, attributes):
    """Create a properly configured LogRecord with valid trace and span IDs."""
    # Map text severity to SeverityNumber
    severity_map = {
        "ERROR": SeverityNumber.ERROR,
        "WARN": SeverityNumber.WARN,
        "WARNING": SeverityNumber.WARN,
        "INFO": SeverityNumber.INFO,
        "DEBUG": SeverityNumber.DEBUG,
        "TRACE": SeverityNumber.TRACE,
        "FATAL": SeverityNumber.FATAL
    }
    
    # Ensure severity is properly capitalized and mapped to a number
    severity_text = severity.upper() if isinstance(severity, str) else "INFO"
    severity_number = severity_map.get(severity_text, SeverityNumber.INFO)
    
    # Ensure trace_flags is properly initialized
    trace_flags = TraceFlags(0)
    
    # Create a copy of attributes to avoid modifying the original
    attrs = dict(attributes or {})
    # Add severity information to attributes for easier filtering in Loki
    attrs["level"] = severity_text
    attrs["severity_number"] = severity_number.value
    
    try:
        # Create log record with all required fields properly set
        return LogRecord(
            timestamp=timestamp,
            observed_timestamp=timestamp,
            body=str(body),  # Ensure body is a string
            severity_text=severity_text,
            severity_number=severity_number,  # Pass enum directly
            attributes=attrs,
            trace_id=INVALID_TRACE_ID,
            span_id=INVALID_SPAN_ID,
            trace_flags=trace_flags,
            resource=resource  # Use the resource from config instead of None
        )
    except Exception as e:
        logger.error(f"Error creating LogRecord: {e}")
        # Fallback to simpler LogRecord if there's an error
        return LogRecord(
            timestamp=timestamp,
            observed_timestamp=timestamp,
            body=str(body),
            severity_text="INFO",
            severity_number=SeverityNumber.INFO,  # Always include a severity number
            attributes={"level": "INFO", "severity_number": SeverityNumber.INFO.value},
            trace_id=INVALID_TRACE_ID,
            span_id=INVALID_SPAN_ID,
            trace_flags=trace_flags,
            resource=resource  # Use the resource from config instead of None
        )