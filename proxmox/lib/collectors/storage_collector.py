#!/usr/bin/env python3
"""
Storage metrics collector for Proxmox OpenTelemetry Monitoring
"""
import json
import re
from lib.config import logger
from lib.utils import run_command

def collect_storage_metrics(storage_status=None, storage_usage=None, 
                          storage_used=None, storage_total=None):
    """Collect Proxmox storage metrics."""
    logger.info("Collecting Proxmox storage metrics")
    storage_metrics = []
    
    # Get list of all storages using the Proxmox API
    storage_list = run_command("pvesh get /storage -output-format json")
    if storage_list:
        try:
            storages = json.loads(storage_list)
            for storage in storages:
                try:
                    storage_id = storage.get('storage')
                    storage_type = storage.get('type', 'unknown')
                    storage_active = storage.get('active', 0)
                    
                    if not storage_id:
                        continue
                    
                    # Create labels for this storage
                    storage_labels = {
                        "storage": storage_id,
                        "type": storage_type,
                        "content": ",".join(storage.get('content', [])),
                    }
                    
                    storage_data = {
                        'id': storage_id,
                        'type': storage_type,
                        'active': bool(storage_active),
                        'content': storage.get('content', []),
                        'labels': storage_labels
                    }
                    
                    # Send storage status metric (1=active, 0=inactive)
                    if storage_status:
                        storage_status.set(1 if storage_active else 0, storage_labels)
                    
                    # Skip detailed usage/capacity for ZFS storages (handled by ZFS collector)
                    if storage_type == "zfspool":
                        logger.info(f"Skipping usage/capacity for ZFS storage {storage_id} (handled by ZFS collector)")
                        storage_metrics.append(storage_data)
                        continue
                    
                    # Get detailed storage info
                    storage_details_cmd = f"pvesh get /nodes/`hostname`/storage/{storage_id}/status -output-format json"
                    storage_details = run_command(storage_details_cmd)
                    
                    if storage_details:
                        details = json.loads(storage_details)
                        
                        # Get usage data if available
                        if 'total' in details and 'used' in details and details.get('total', 0) > 0:
                            total_bytes = details.get('total', 0)
                            used_bytes = details.get('used', 0)
                            avail_bytes = details.get('avail', 0)
                            
                            # Calculate percentage
                            used_percent = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                            
                            storage_data['usage'] = {
                                'total': total_bytes,
                                'used': used_bytes,
                                'avail': avail_bytes,
                                'percent': used_percent
                            }
                            
                            # Send storage usage metrics
                            if storage_usage:
                                storage_usage.set(used_percent, storage_labels)
                            if storage_total:
                                storage_total.set(total_bytes, storage_labels)
                            if storage_used:
                                storage_used.set(used_bytes, storage_labels)
                            
                            logger.info(f"Storage {storage_id} ({storage_type}): {used_percent:.1f}% ({used_bytes/(1024**3):.1f}GB/{total_bytes/(1024**3):.1f}GB)")
                        else:
                            logger.info(f"Storage {storage_id} ({storage_type}): no usage data available")
                        
                        storage_metrics.append(storage_data)
                    
                except Exception as e:
                    logger.error(f"Error processing storage data: {e}")
        
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing storage list JSON: {e}")
    
    return storage_metrics


def collect_disk_smart_metrics(smart_metrics=None):
    """Collect SMART metrics for physical disks."""
    logger.info("Collecting disk SMART metrics")
    smart_data = {}
    
    # Get list of physical disks
    lsblk_output = run_command("lsblk -d -o NAME,TYPE,SIZE -J")
    if not lsblk_output:
        logger.error("Failed to get disk list")
        return smart_data
    
    try:
        disks = json.loads(lsblk_output)["blockdevices"]
        physical_disks = [disk["name"] for disk in disks if disk["type"] == "disk"]
        
        for disk in physical_disks:
            # Skip loop, ram, sr devices, and ZFS virtual devices (zd*)
            if disk.startswith(('loop', 'ram', 'sr', 'zd')):
                logger.debug(f"Skipping non-physical or unsupported device: /dev/{disk}")
                continue
                
            # Get SMART data in JSON format
            smartctl_output = run_command(f"smartctl -a -j /dev/{disk}")
            if not smartctl_output:
                logger.info(f"No SMART data for disk /dev/{disk}")
                continue
                
            try:
                disk_smart = json.loads(smartctl_output)
                disk_model = disk_smart.get("model_name", "Unknown")
                disk_serial = disk_smart.get("serial_number", "Unknown")
                
                # Create basic disk info
                smart_data[disk] = {
                    "model": disk_model,
                    "serial": disk_serial,
                    "attributes": {}
                }
                
                # Process SMART attributes if available
                if "ata_smart_attributes" in disk_smart and "table" in disk_smart["ata_smart_attributes"]:
                    for attr in disk_smart["ata_smart_attributes"]["table"]:
                        attr_id = attr.get("id")
                        attr_name = attr.get("name", f"Unknown_{attr_id}")
                        attr_value = attr.get("value")
                        attr_raw = attr.get("raw", {}).get("value")
                        attr_thresh = attr.get("thresh")
                        attr_worst = attr.get("worst")
                        
                        # Clean up attribute name
                        attr_name_clean = re.sub(r'[^a-zA-Z0-9_]', '_', attr_name).lower()
                        
                        # Store the attribute data
                        smart_data[disk]["attributes"][attr_name_clean] = {
                            "id": attr_id,
                            "name": attr_name,
                            "value": attr_value,
                            "raw": attr_raw,
                            "threshold": attr_thresh,
                            "worst": attr_worst
                        }
                        
                        # Create labels for this attribute
                        labels = {
                            "device": disk,
                            "model": disk_model,
                            "serial": disk_serial,
                            "attribute_id": str(attr_id),
                            "attribute_name": attr_name_clean,
                        }
                        
                        # Send normalized value metric
                        if smart_metrics and attr_value is not None:
                            smart_metrics.set(attr_value, dict(labels, **{"type": "normalized"}))
                        
                        # Send raw value metric for some useful attributes
                        if smart_metrics and attr_raw is not None:
                            smart_metrics.set(attr_raw, dict(labels, **{"type": "raw"}))
                
                # Process NVMe SMART attributes if available
                elif "nvme_smart_health_information_log" in disk_smart:
                    logger.info(f"Processing NVMe SMART for /dev/{disk}")
                    nvme_log = disk_smart["nvme_smart_health_information_log"]
                    
                    # Create base labels for NVMe attributes
                    labels_base = {
                        "device": disk,
                        "model": disk_model,
                        "serial": disk_serial,
                    }
                    
                    # Report key NVMe SMART attributes
                    if smart_metrics and "data_units_written" in nvme_log:
                        smart_metrics.set(nvme_log["data_units_written"], 
                                        dict(labels_base, attribute_name="nvme_data_units_written", type="raw"))
                    
                    if smart_metrics and "data_units_read" in nvme_log:
                        smart_metrics.set(nvme_log["data_units_read"], 
                                        dict(labels_base, attribute_name="nvme_data_units_read", type="raw"))
                    
                    if smart_metrics and "power_on_hours" in nvme_log:
                        smart_metrics.set(nvme_log["power_on_hours"], 
                                        dict(labels_base, attribute_name="nvme_power_on_hours", type="raw"))
                    
                    if smart_metrics and "media_errors" in nvme_log:
                        smart_metrics.set(nvme_log["media_errors"], 
                                        dict(labels_base, attribute_name="nvme_media_errors", type="raw"))
                    
                    if smart_metrics and "critical_warning" in nvme_log:
                        smart_metrics.set(nvme_log["critical_warning"], 
                                        dict(labels_base, attribute_name="nvme_critical_warning", type="raw"))
                    
                    # Store these values in our return structure too
                    for key, value in nvme_log.items():
                        if isinstance(value, (int, float)):
                            attr_name_clean = f"nvme_{key}"
                            smart_data[disk]["attributes"][attr_name_clean] = {
                                "name": key,
                                "raw": value
                            }
                
                # Extract temperature from SMART data if available
                if "temperature" in disk_smart and "current" in disk_smart["temperature"]:
                    temp = disk_smart["temperature"]["current"]
                    smart_data[disk]["temperature"] = temp
                    
                    # Create labels for temperature
                    temp_labels = {
                        "device": disk,
                        "model": disk_model,
                        "serial": disk_serial,
                        "attribute_name": "temperature",
                        "type": "raw"
                    }
                    
                    # Send temperature as a separate metric
                    if smart_metrics:
                        smart_metrics.set(temp, temp_labels)
                    
                    logger.info(f"Disk {disk} ({disk_model}) temperature: {temp}°C")
                
                # Log other important SMART metrics
                logger.info(f"Disk {disk} ({disk_model}, S/N: {disk_serial}) SMART status: {_get_smart_health_status(disk_smart)}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing SMART data for disk {disk}: {e}")
    
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing disk list JSON: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while collecting SMART metrics: {e}")
    
    return smart_data


def _get_smart_health_status(smart_data):
    """Extract the overall health status from SMART data."""
    if "smart_status" in smart_data and "passed" in smart_data["smart_status"]:
        return "PASSED" if smart_data["smart_status"]["passed"] else "FAILED"
    return "UNKNOWN"