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

def run_command(command, timeout=30, shell=True):
    """Run a shell command and return the output.
    
    Args:
        command (str): Command to execute
        timeout (int): Maximum execution time in seconds before aborting
        shell (bool): Whether to use shell execution (required for pipes, redirects)
        
    Returns:
        str: Command output on success, None on failure
    """
    try:
        # If shell=False is specified and command is a string, split it into arguments list
        cmd = command
        if not shell and isinstance(command, str):
            import shlex
            cmd = shlex.split(command)
            
        result = subprocess.run(
            cmd,
            shell=shell,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout  # Add timeout to prevent hanging
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command '{command}' timed out after {timeout} seconds")
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"Command '{command}' failed with exit code {e.returncode}: {e}")
        logger.error(f"Command stderr: {e.stderr}")
        return None

def create_log_record(timestamp, body, severity, attributes=None, observed_timestamp=None):
    """Create a properly configured LogRecord with valid trace and span IDs.
    
    Args:
        timestamp (int): Original event timestamp in nanoseconds
        body (str): Log message content
        severity (str or SeverityNumber): Log severity level
        attributes (dict): Additional attributes to include with the log
        observed_timestamp (int): When the event was observed (defaults to timestamp if None)
        
    Returns:
        LogRecord: Configured OpenTelemetry LogRecord object
    """
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
    
    # Handle different severity input types
    if isinstance(severity, SeverityNumber):
        severity_number = severity
        severity_text = severity.name  # Use the enum name as text
    elif isinstance(severity, str):
        severity_text = severity.upper()
        severity_number = severity_map.get(severity_text, SeverityNumber.INFO)
    else:
        severity_text = "INFO"
        severity_number = SeverityNumber.INFO
    
    # Use the timestamp as observed_timestamp if not provided
    actual_observed_timestamp = observed_timestamp if observed_timestamp is not None else timestamp
    
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
            observed_timestamp=actual_observed_timestamp,
            body=str(body),  # Ensure body is a string
            severity_text=severity_text,
            severity_number=severity_number,  # Pass enum directly
            attributes=attrs,
            trace_id=INVALID_TRACE_ID,
            span_id=INVALID_SPAN_ID,
            trace_flags=trace_flags,
            resource=resource  # Use the resource from config
        )
    except Exception as e:
        logger.error(f"Error creating LogRecord: {e}")
        # Preserve original attributes in fallback, adding only required severity info
        fallback_attrs = dict(attributes or {})
        fallback_attrs["level"] = "INFO"
        fallback_attrs["severity_number"] = SeverityNumber.INFO.value
        
        # Fallback to simpler LogRecord if there's an error
        return LogRecord(
            timestamp=timestamp,
            observed_timestamp=actual_observed_timestamp,
            body=str(body),
            severity_text="INFO",
            severity_number=SeverityNumber.INFO,
            attributes=fallback_attrs,
            trace_id=INVALID_TRACE_ID,
            span_id=INVALID_SPAN_ID,
            trace_flags=trace_flags,
            resource=resource
        )