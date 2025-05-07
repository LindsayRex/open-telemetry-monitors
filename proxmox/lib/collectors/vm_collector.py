#!/usr/bin/env python3
"""
VM metrics collector for Proxmox OpenTelemetry Monitoring
"""
import json
from lib.config import logger
from lib.utils import run_command

def collect_vm_metrics(vm_status=None, vm_cpu_usage=None, vm_memory_usage=None):
    """Collect metrics from Proxmox VMs."""
    logger.info("Collecting VM metrics")
    vm_metrics = []
    
    # Get list of all VMs using the Proxmox API
    vm_list = run_command("pvesh get /cluster/resources --type vm -output-format json")
    if vm_list:
        try:
            vms = json.loads(vm_list)
            for vm in vms:
                try:
                    vm_id = vm.get('vmid')
                    vm_name = vm.get('name', f"vm-{vm_id}")
                    vm_status_val = vm.get('status', 'unknown')
                    
                    if not vm_id:
                        continue
                    
                    # Create labels for this VM
                    vm_labels = {
                        "vmid": str(vm_id),
                        "name": vm_name,
                        "type": vm.get('type', 'unknown')
                    }
                    
                    vm_data = {
                        'id': vm_id,
                        'name': vm_name,
                        'status': vm_status_val,
                        'labels': vm_labels,
                        'running': vm_status_val == 'running'
                    }
                    
                    # Send VM status metric (1=running, 0=stopped)
                    if vm_status:
                        status_value = 1 if vm_status_val == 'running' else 0
                        vm_status.set(status_value, vm_labels)
                    
                    # Skip detailed metrics for non-running VMs
                    if vm_status_val == 'running':
                        # Get CPU usage
                        cpu_val = vm.get('cpu', 0)
                        vm_data['cpu'] = cpu_val
                        
                        # Send VM CPU usage metric
                        if vm_cpu_usage:
                            vm_cpu_usage.set(cpu_val * 100, vm_labels)  # Convert to percentage
                        
                        # Get memory usage
                        if 'mem' in vm and 'maxmem' in vm and vm.get('maxmem', 0) > 0:
                            mem_used = vm.get('mem', 0)
                            mem_total = vm.get('maxmem', 1)
                            mem_percent = (mem_used / mem_total) * 100
                            vm_data['memory'] = {
                                'used': mem_used,
                                'total': mem_total,
                                'percent': mem_percent
                            }
                            
                            # Send VM memory usage metric
                            if vm_memory_usage:
                                vm_memory_usage.set(mem_percent, vm_labels)
                    
                    vm_metrics.append(vm_data)
                    logger.info(f"VM {vm_name} (ID: {vm_id}): status={vm_status_val}, CPU={vm.get('cpu', 0):.2f}")
                    
                except Exception as e:
                    logger.error(f"Error processing VM data: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing VM list JSON: {e}")
    
    return vm_metrics