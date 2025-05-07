#!/usr/bin/env python3
"""
Temperature metrics collector for Proxmox OpenTelemetry Monitoring
"""
import json
import time
from lib.config import (
    logger, TEMP_CRITICAL_THRESHOLD,
    DISK_TEMP_WARNING_THRESHOLD
)
from lib.utils import run_command, create_log_record

def collect_temperature_metrics(temperature_gauge, logger_otel):
    """Collect comprehensive temperature metrics from all available sensors."""
    logger.info("Collecting temperature metrics")
    temp_metrics = {}
    
    # Get sensor data using lm-sensors with JSON output
    sensors_output = run_command("sensors -j")
    if not sensors_output:
        logger.error("Failed to get sensors data")
        return temp_metrics
    
    try:
        sensors_data = json.loads(sensors_output)
        
        # Process each adapter type
        for adapter_name, adapter_data in sensors_data.items():
            # CPU cores temperature (coretemp)
            if "coretemp" in adapter_name:
                # Process CPU Package temperature
                if "Package id 0" in adapter_data:
                    package_data = adapter_data["Package id 0"]
                    # Find the temperature input key (usually temp1_input)
                    temp_key = next((k for k in package_data.keys() if k.endswith("_input") and k.startswith("temp")), None)
                    
                    if temp_key:
                        package_temp = package_data.get(temp_key, 0)
                        # Find max and crit temps
                        max_key = temp_key.replace("_input", "_max")
                        crit_key = temp_key.replace("_input", "_crit")
                        
                        package_high = package_data.get(max_key, 85.0)
                        package_crit = package_data.get(crit_key, 105.0)
                        
                        # Set metric
                        if temperature_gauge:
                            temperature_gauge.set(package_temp, {
                                "source": "cpu",
                                "type": "package",
                                "name": "package_id_0",
                                "high": str(package_high),
                                "critical": str(package_crit)
                            })
                        
                        temp_metrics["cpu_package"] = {
                            "temperature": package_temp,
                            "high": package_high,
                            "critical": package_crit
                        }
                        
                        logger.info(f"CPU Package: {package_temp}°C (High: {package_high}°C, Critical: {package_crit}°C)")
                        
                        # Alert on critical temperature
                        if package_temp >= package_crit - TEMP_CRITICAL_THRESHOLD:
                            if logger_otel:
                                log_record = create_log_record(
                                    timestamp=int(time.time() * 1e9),
                                    body=f"ALERT: CPU package temperature critical: {package_temp}°C",
                                    severity="ERROR",
                                    attributes={
                                        "event.type": "alert",
                                        "sensor": "cpu_package",
                                        "temperature": package_temp,
                                        "critical": package_crit
                                    }
                                )
                                logger_otel.emit(log_record)
                
                # Process individual CPU cores - using proper temp key pattern
                for key, value in adapter_data.items():
                    if key.startswith("Core "):
                        core_num = key.split()[1]
                        # Find any temperature input key (may be temp2_input, temp6_input, etc.)
                        temp_key = next((k for k in value.keys() if k.endswith("_input") and k.startswith("temp")), None)
                        
                        if temp_key:
                            temp = value.get(temp_key, 0)
                            # Find max and crit temps
                            max_key = temp_key.replace("_input", "_max")
                            crit_key = temp_key.replace("_input", "_crit")
                            
                            high = value.get(max_key, 85.0)
                            crit = value.get(crit_key, 105.0)
                            
                            # Set metric
                            if temperature_gauge:
                                temperature_gauge.set(temp, {
                                    "source": "cpu",
                                    "type": "core",
                                    "name": f"core_{core_num}",
                                    "high": str(high),
                                    "critical": str(crit)
                                })
                            
                            temp_metrics[f"cpu_core_{core_num}"] = {
                                "temperature": temp,
                                "high": high,
                                "critical": crit
                            }
                            
                            # Log all core temperatures
                            logger.info(f"CPU Core {core_num}: {temp}°C (High: {high}°C, Critical: {crit}°C)")
                            
                            # Alert on critical temperature
                            if temp >= crit - TEMP_CRITICAL_THRESHOLD:
                                if logger_otel:
                                    log_record = create_log_record(
                                        timestamp=int(time.time() * 1e9),
                                        body=f"ALERT: CPU Core {core_num} temperature critical: {temp}°C",
                                        severity="ERROR",
                                        attributes={
                                            "event.type": "alert",
                                            "sensor": f"cpu_core_{core_num}",
                                            "temperature": temp,
                                            "critical": crit
                                        }
                                    )
                                    logger_otel.emit(log_record)
            
            # NVMe drive temperature
            elif "nvme" in adapter_name:
                # Process composite temperature
                if "Composite" in adapter_data:
                    nvme_data = adapter_data["Composite"]
                    # Find any temperature input key
                    temp_key = next((k for k in nvme_data.keys() if k.endswith("_input") and k.startswith("temp")), None)
                    
                    if temp_key:
                        temp = nvme_data.get(temp_key, 0)
                        # Find related thresholds
                        low_key = temp_key.replace("_input", "_min") if temp_key.replace("_input", "_min") in nvme_data else temp_key.replace("_input", "_low")
                        high_key = temp_key.replace("_input", "_max") if temp_key.replace("_input", "_max") in nvme_data else temp_key.replace("_input", "_high")
                        crit_key = temp_key.replace("_input", "_crit")
                        
                        low = nvme_data.get(low_key, -273.1)
                        high = nvme_data.get(high_key, 85.0)
                        crit = nvme_data.get(crit_key, 85.0)
                        
                        # Device name formatting
                        device_name = adapter_name.replace('-', '_')
                        
                        # Set metric
                        if temperature_gauge:
                            temperature_gauge.set(temp, {
                                "source": "nvme",
                                "type": "composite",
                                "name": device_name,
                                "high": str(high),
                                "critical": str(crit)
                            })
                        
                        temp_metrics[f"nvme_{device_name}_composite"] = {
                            "temperature": temp,
                            "low": low,
                            "high": high,
                            "critical": crit
                        }
                        
                        logger.info(f"NVMe {device_name} Composite: {temp}°C (High: {high}°C, Critical: {crit}°C)")
                        
                        # Alert on high temperature
                        if temp >= crit - DISK_TEMP_WARNING_THRESHOLD:
                            if logger_otel:
                                log_record = create_log_record(
                                    timestamp=int(time.time() * 1e9),
                                    body=f"ALERT: NVMe {device_name} temperature high: {temp}°C",
                                    severity="ERROR",
                                    attributes={
                                        "event.type": "alert",
                                        "sensor": f"nvme_{device_name}",
                                        "temperature": temp,
                                        "critical": crit
                                    }
                                )
                                logger_otel.emit(log_record)
                
                # Process individual NVMe sensors
                for sensor_name, sensor_data in adapter_data.items():
                    if sensor_name.startswith("Sensor "):
                        sensor_num = sensor_name.split()[1]
                        # Find any temperature input key
                        if isinstance(sensor_data, dict):  # Ensure it's a dictionary
                            temp_key = next((k for k in sensor_data.keys() if k.endswith("_input") and k.startswith("temp")), None)
                            
                            if temp_key:
                                temp = sensor_data.get(temp_key, 0)
                                # Find thresholds
                                low_key = temp_key.replace("_input", "_min") if temp_key.replace("_input", "_min") in sensor_data else temp_key.replace("_input", "_low")
                                high_key = temp_key.replace("_input", "_max") if temp_key.replace("_input", "_max") in sensor_data else temp_key.replace("_input", "_high")
                                
                                low = sensor_data.get(low_key, -273.1)
                                high = sensor_data.get(high_key, 65261.8)
                                
                                # Device name formatting
                                device_name = adapter_name.replace('-', '_')
                                
                                # Set metric
                                if temperature_gauge:
                                    temperature_gauge.set(temp, {
                                        "source": "nvme",
                                        "type": "sensor",
                                        "name": f"{device_name}_sensor_{sensor_num}",
                                        "high": str(high)
                                    })
                                
                                temp_metrics[f"nvme_{device_name}_sensor_{sensor_num}"] = {
                                    "temperature": temp,
                                    "low": low,
                                    "high": high
                                }
                                
                                logger.info(f"NVMe {device_name} Sensor {sensor_num}: {temp}°C")
            
            # ACPI temperature sensors
            elif "acpitz" in adapter_name:
                _collect_acpi_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics)
            
            # Gigabyte WMI sensors
            elif "gigabyte_wmi" in adapter_name:
                _collect_gigabyte_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics)
            
            # Any other sensors not explicitly handled
            else:
                _collect_other_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics)
    
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing sensors JSON output: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while collecting temperature metrics: {e}")
    
    return temp_metrics


def _collect_acpi_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics):
    """Helper function to collect ACPI temperature sensors."""
    for temp_key, temp_data in adapter_data.items():
        if temp_key.startswith("temp") and isinstance(temp_data, dict):
            # Find temperature input
            input_key = next((k for k in temp_data.keys() if "input" in k), None)
            
            if input_key:
                temp = temp_data.get(input_key, 0)
                
                # Set metric
                if temperature_gauge:
                    temperature_gauge.set(temp, {
                        "source": "acpi",
                        "type": "temp",
                        "name": f"acpi_{temp_key}"
                    })
                
                temp_metrics[f"acpi_{temp_key}"] = {
                    "temperature": temp
                }
                
                logger.info(f"ACPI {temp_key}: {temp}°C")


def _collect_gigabyte_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics):
    """Helper function to collect Gigabyte WMI temperature sensors."""
    # These have a different structure, with direct temp keys
    for key, value in adapter_data.items():
        if key.startswith("temp") and key != "Adapter":
            if isinstance(value, (int, float)):
                # Direct temperature value
                temp = value
                
                # Set metric
                if temperature_gauge:
                    temperature_gauge.set(temp, {
                        "source": "gigabyte_wmi",
                        "type": "temp",
                        "name": f"gigabyte_{key}"
                    })
                
                temp_metrics[f"gigabyte_{key}"] = {
                    "temperature": temp
                }
                
                logger.info(f"Gigabyte WMI {key}: {temp}°C")


def _collect_other_temps(adapter_name, adapter_data, temperature_gauge, temp_metrics):
    """Helper function to collect other temperature sensors."""
    # Try to find temperature readings in other sensors
    for key, value in adapter_data.items():
        if key != "Adapter" and isinstance(value, dict):
            # Look for keys containing "temp" and "input"
            temp_keys = [k for k in value.keys() if "temp" in k.lower() and "input" in k.lower()]
            
            for temp_key in temp_keys:
                sensor_name = f"{adapter_name}_{key}_{temp_key}"
                temp = value.get(temp_key)
                
                if isinstance(temp, (int, float)):
                    # Set metric
                    if temperature_gauge:
                        temperature_gauge.set(temp, {
                            "source": "other",
                            "type": "temp",
                            "name": sensor_name.replace('-', '_')
                        })
                    
                    temp_metrics[sensor_name.replace('-', '_')] = {
                        "temperature": temp
                    }
                    
                    logger.info(f"Other sensor {sensor_name}: {temp}°C")