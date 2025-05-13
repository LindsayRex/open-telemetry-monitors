#!/usr/bin/env python3
"""
Temperature metrics collector for Proxmox OpenTelemetry Monitoring

This module collects temperature data from various sensors including:
- CPU packages and cores (including multi-socket systems)
- NVMe drives
- ACPI thermal zones
- Motherboard sensors (including Gigabyte WMI)
- Other miscellaneous temperature sensors
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
                # Process CPU Package temperatures - enhanced for multi-socket systems
                # Iterate over all items in adapter_data to find CPU Packages
                for package_key, package_data in adapter_data.items():
                    if package_key.startswith("Package id "):
                        try:
                            package_id_str = package_key.replace("Package id ", "")  # Extracts "0", "1", etc.
                            
                            # Find the temperature input key (usually temp1_input)
                            temp_key = next((k for k in package_data.keys() if k.endswith("_input") and k.startswith("temp")), None)
                            
                            if temp_key:
                                package_temp = package_data.get(temp_key, 0)
                                # Find max and crit temps
                                max_key = temp_key.replace("_input", "_max")
                                crit_key = temp_key.replace("_input", "_crit")
                                
                                # Use more robust default values and handle missing thresholds
                                package_high = package_data.get(max_key)
                                package_crit = package_data.get(crit_key)
                                
                                # Skip reporting invalid temperatures
                                if package_temp is None or not isinstance(package_temp, (int, float)) or package_temp <= 0:
                                    logger.warning(f"Invalid temperature value for CPU {package_key}: {package_temp}")
                                    continue
                                
                                # Use sensible defaults only if thresholds are missing
                                if package_high is None or not isinstance(package_high, (int, float)) or package_high <= 0:
                                    package_high = 85.0  # Common high threshold
                                    
                                if package_crit is None or not isinstance(package_crit, (int, float)) or package_crit <= 0:
                                    package_crit = 105.0  # Common critical threshold
                                
                                # Add socket information to the attributes for better organization in dashboards
                                # Set metric
                                if temperature_gauge:
                                    temperature_gauge.set(package_temp, {
                                        "source": "cpu",
                                        "type": "package",
                                        "name": f"package_id_{package_id_str}",
                                        "high": str(package_high),
                                        "critical": str(package_crit)
                                    })
                                
                                temp_metrics[f"cpu_package_{package_id_str}"] = {
                                    "temperature": package_temp,
                                    "high": package_high,
                                    "critical": package_crit
                                }
                                
                                logger.info(f"CPU Package {package_id_str}: {package_temp}°C (High: {package_high}°C, Critical: {package_crit}°C)")
                                
                                # Alert on critical temperature
                                if package_temp >= package_crit - TEMP_CRITICAL_THRESHOLD:
                                    if logger_otel:
                                        log_record = create_log_record(
                                            timestamp=int(time.time() * 1e9),
                                            body=f"ALERT: CPU Package {package_id_str} temperature critical: {package_temp}°C",
                                            severity="ERROR",
                                            attributes={
                                                "event.type": "alert",
                                                "sensor": f"cpu_package_{package_id_str}",
                                                "temperature": package_temp,
                                                "critical": package_crit
                                            }
                                        )
                                        logger_otel.emit(log_record)
                            else:
                                logger.warning(f"No temperature input key found for CPU {package_key} in {adapter_name}")
                        except Exception as e:
                            logger.error(f"Error processing CPU {package_key} data in {adapter_name}: {e}")
                            continue  # Continue to the next package if one fails
                
                # Process individual CPU cores with enhanced error handling
                for key, value in adapter_data.items():
                    if key.startswith("Core "):
                        try:
                            core_num = key.split()[1]
                            
                            # Ensure value is a dictionary
                            if not isinstance(value, dict):
                                logger.warning(f"Core data for {key} is not a dictionary in {adapter_name}. Skipping.")
                                continue
                            
                            # Find any temperature input key (may be temp2_input, temp6_input, etc.)
                            temp_key = next((k for k in value.keys() if k.endswith("_input") and k.startswith("temp")), None)
                            
                            if temp_key:
                                temp = value.get(temp_key)
                                # Find max and crit temps
                                max_key = temp_key.replace("_input", "_max")
                                crit_key = temp_key.replace("_input", "_crit")
                                
                                # Use more robust default values and handle missing thresholds
                                high = value.get(max_key)
                                crit = value.get(crit_key)
                                
                                # Skip reporting invalid temperatures
                                if temp is None or not isinstance(temp, (int, float)) or temp <= 0:
                                    logger.warning(f"Invalid temperature value for CPU {key}: {temp}")
                                    continue
                                
                                # Use sensible defaults only if thresholds are missing
                                if high is None or not isinstance(high, (int, float)) or high <= 0:
                                    high = 85.0  # Common high threshold
                                    
                                if crit is None or not isinstance(crit, (int, float)) or crit <= 0:
                                    crit = 105.0  # Common critical threshold
                                
                                # Add socket identifier if available
                                # This is more robust for multi-socket systems where the same core number
                                # might appear in different sockets
                                socket_id = None
                                
                                # Try to determine which socket/package this core belongs to
                                # This approach is based on common core numbering in multi-socket systems
                                # where cores are numbered sequentially across all sockets
                                try:
                                    core_int = int(core_num)
                                    # Look for all Package id entries to determine core-to-socket mapping
                                    package_count = sum(1 for k in adapter_data.keys() if k.startswith("Package id "))
                                    if package_count > 1:
                                        # Simple heuristic: in dual-socket systems, first half of cores belong to socket 0
                                        # This is a simplification and may need adjustment for specific systems
                                        cores_per_socket = len([k for k in adapter_data.keys() if k.startswith("Core ")]) // package_count
                                        if cores_per_socket > 0:
                                            socket_id = str(core_int // cores_per_socket)
                                except (ValueError, ZeroDivisionError):
                                    # If we can't determine the socket, don't add socket information
                                    pass
                                
                                # Set metric with enhanced attributes
                                if temperature_gauge:
                                    attributes = {
                                        "source": "cpu",
                                        "type": "core",
                                        "name": f"core_{core_num}",
                                        "high": str(high),
                                        "critical": str(crit)
                                    }
                                    
                                    # Add socket information if available
                                    if socket_id is not None:
                                        attributes["socket"] = socket_id
                                        
                                    temperature_gauge.set(temp, attributes)
                                
                                core_metrics = {
                                    "temperature": temp,
                                    "high": high,
                                    "critical": crit
                                }
                                
                                # Add socket information if available
                                if socket_id is not None:
                                    core_metrics["socket"] = socket_id
                                    
                                temp_metrics[f"cpu_core_{core_num}"] = core_metrics
                                
                                # Log all core temperatures with socket info if available
                                socket_info = f" (Socket {socket_id})" if socket_id is not None else ""
                                logger.info(f"CPU Core {core_num}{socket_info}: {temp}°C (High: {high}°C, Critical: {crit}°C)")
                                
                                # Alert on critical temperature
                                if temp >= crit - TEMP_CRITICAL_THRESHOLD:
                                    if logger_otel:
                                        alert_attributes = {
                                            "event.type": "alert",
                                            "sensor": f"cpu_core_{core_num}",
                                            "temperature": temp,
                                            "critical": crit
                                        }
                                        
                                        # Add socket information to alert if available
                                        if socket_id is not None:
                                            alert_attributes["socket"] = socket_id
                                            
                                        log_record = create_log_record(
                                            timestamp=int(time.time() * 1e9),
                                            body=f"ALERT: CPU Core {core_num}{socket_info} temperature critical: {temp}°C",
                                            severity="ERROR",
                                            attributes=alert_attributes
                                        )
                                        logger_otel.emit(log_record)
                            else:
                                logger.warning(f"No temperature input key found for CPU {key} in {adapter_name}")
                        except Exception as e:
                            logger.error(f"Error processing CPU {key} data in {adapter_name}: {e}")
                            continue  # Continue to the next core if one fails
            
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
                                high_val = sensor_data.get(high_key)
                                
                                # Handle unrealistic high threshold values (like 65261.8)
                                if high_val is None or high_val > 200:
                                    high = 85.0  # Use sensible default instead of unrealistic value
                                    high_attr = "N/A"  # Mark as not available in attributes
                                else:
                                    high = high_val
                                    high_attr = str(high)
                                
                                # Device name formatting
                                device_name = adapter_name.replace('-', '_')
                                
                                # Set metric
                                if temperature_gauge:
                                    temperature_gauge.set(temp, {
                                        "source": "nvme",
                                        "type": "sensor",
                                        "name": f"{device_name}_sensor_{sensor_num}",
                                        "high": high_attr
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
    """Helper function to collect Gigabyte WMI temperature sensors with known mapping."""
    logger.info(f"Processing Gigabyte WMI: {adapter_name}")
    
    # Mapping from WMI tempX key to descriptive BIOS/Smart Fan name
    wmi_temp_mapping = {
        "temp1": "System2",       # System 2
        "temp2": "PCH",           # PCH
        "temp3": "CPU_Socket",    # CPU (Likely CPU socket/motherboard area near CPU)
        "temp4": "PCIEX16_Slot",  # PCIEX16
        "temp5": "PCIEX4_Slot",   # PCIEX4
        "temp6": "VRM_MOS"        # VRM MOS
    }
    
    for sensor_outer_key, sensor_data_dict in adapter_data.items():
        # Skip the "Adapter" key and process only temp entries
        if sensor_outer_key != "Adapter" and sensor_outer_key.startswith("temp") and isinstance(sensor_data_dict, dict):
            try:
                # Find the key ending with _input (e.g., temp1_input, temp2_input)
                input_key = next((k for k in sensor_data_dict.keys() if k.endswith("_input") and k.startswith("temp")), None)
                
                if input_key:
                    temp = sensor_data_dict.get(input_key)
                    
                    if temp is not None and isinstance(temp, (int, float)):
                        # Get the descriptive name from the mapping
                        descriptive_name_raw = wmi_temp_mapping.get(sensor_outer_key)
                        
                        if descriptive_name_raw:
                            # Clean up the descriptive name for use in metric attributes
                            descriptive_name_metric = descriptive_name_raw.replace(" ", "_").replace("/", "_").lower()
                        else:
                            # Fallback if tempX is not in our map
                            descriptive_name_metric = f"unknown_wmi_{sensor_outer_key}"
                            logger.warning(f"Gigabyte WMI sensor {sensor_outer_key} not found in mapping. Using default name.")

                        if temperature_gauge:
                            temperature_gauge.set(temp, {
                                "source": "gigabyte_wmi",
                                "type": "motherboard_sensor",
                                "name": descriptive_name_metric
                            })
                        
                        temp_metrics[f"gigabyte_wmi_{descriptive_name_metric}"] = {
                            "temperature": temp
                        }
                        logger.info(f"Gigabyte WMI - {descriptive_name_raw} ({sensor_outer_key}): {temp}°C")
                    else:
                        logger.warning(f"Invalid or missing temperature value for Gigabyte WMI {sensor_outer_key} (key: {input_key})")
                else:
                    logger.warning(f"No input key found for Gigabyte WMI sensor {sensor_outer_key}")
            except Exception as e:
                logger.error(f"Error processing Gigabyte WMI sensor {sensor_outer_key}: {e}")
                continue


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