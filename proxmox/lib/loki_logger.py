#!/usr/bin/env python3
"""
Direct Loki logger for Proxmox OpenTelemetry Monitoring
"""
import json
import time
import logging
import requests
from datetime import datetime

class LokiLogger:
    """A simple direct logger that sends logs to Loki via HTTP"""
    
    def __init__(self, url, labels=None):
        """Initialize the LokiLogger with a URL and default labels"""
        self.url = url
        self.default_labels = labels or {"service": "proxmox-otel"}
        self.logger = logging.getLogger("loki-direct")
        self.session = requests.Session()
    
    def log(self, message, severity="INFO", labels=None, timestamp=None):
        """Send a log entry directly to Loki"""
        current_labels = self.default_labels.copy()
        if labels:
            current_labels.update(labels)
        
        # Use current time in nanoseconds if no timestamp provided
        if timestamp is None:
            timestamp = int(time.time() * 1e9)
        
        # Convert timestamp to string for Loki
        ts_str = str(timestamp)
        
        # Add severity to labels
        current_labels["level"] = severity
        
        # Format data for Loki
        payload = {
            "streams": [
                {
                    "stream": current_labels,
                    "values": [[ts_str, message]]
                }
            ]
        }
        
        try:
            response = self.session.post(
                self.url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=2  # Short timeout to not block monitoring
            )
            if response.status_code >= 400:
                self.logger.error(f"Failed to send log to Loki: {response.status_code} - {response.text}")
                return False
            return True
        except Exception as e:
            self.logger.error(f"Error sending log to Loki: {e}")
            return False
    
    def debug(self, message, labels=None):
        return self.log(message, "DEBUG", labels)
    
    def info(self, message, labels=None):
        return self.log(message, "INFO", labels)
    
    def warn(self, message, labels=None):
        return self.log(message, "WARN", labels)
    
    def warning(self, message, labels=None):
        return self.log(message, "WARN", labels)
    
    def error(self, message, labels=None):
        return self.log(message, "ERROR", labels)
    
    def critical(self, message, labels=None):
        return self.log(message, "CRITICAL", labels)