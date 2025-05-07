#!/usr/bin/env python3
"""
System metrics collector for Proxmox OpenTelemetry Monitoring
"""
import json
import re
import time
from lib.config import logger
from lib.utils import run_command

def collect_system_metrics(cpu_usage=None, memory_usage=None, memory_total=None, 
                          memory_used=None, node_uptime=None, disk_io_read=None, 
                          disk_io_write=None, net_in_bytes=None, net_out_bytes=None):
    """Collect system metrics from Proxmox node."""
    logger.info("Collecting node system metrics")
    system_metrics = {}
    
    try:
        # Get node status from Proxmox API
        node_status = run_command("pvesh get /nodes/`hostname`/status -output-format json")
        if node_status:
            try:
                node_data = json.loads(node_status)
                
                # Node information
                hostname = node_data.get('pveversion', 'unknown').split('/')[-1]
                node_id = node_data.get('node', 'unknown')
                
                # Basic labels for all metrics
                node_labels = {
                    "node": node_id,
                    "hostname": hostname
                }
                
                system_metrics['node'] = {
                    'id': node_id,
                    'hostname': hostname,
                    'pve_version': node_data.get('pveversion', 'unknown'),
                    'kernel_version': node_data.get('kernel', 'unknown'),
                    'uptime': node_data.get('uptime', 0)
                }
                
                # Memory metrics
                if 'memory' in node_data:
                    memory_data = node_data['memory']
                    total_mem = memory_data.get('total', 0)
                    used_mem = memory_data.get('used', 0)
                    free_mem = memory_data.get('free', 0)
                    
                    # Calculate percentage
                    mem_usage_pct = (used_mem / total_mem) * 100 if total_mem > 0 else 0
                    
                    system_metrics['memory'] = {
                        'total': total_mem,
                        'used': used_mem,
                        'free': free_mem,
                        'usage_percent': mem_usage_pct
                    }
                    
                    # Send memory metrics
                    if memory_usage:
                        memory_usage.set(mem_usage_pct, node_labels)
                    if memory_total:
                        memory_total.set(total_mem, node_labels)
                    if memory_used:
                        memory_used.set(used_mem, node_labels)
                    
                    logger.info(f"Memory Usage: {mem_usage_pct:.1f}% ({used_mem/(1024**3):.1f}GB/{total_mem/(1024**3):.1f}GB)")
                
                # CPU metrics
                if 'cpu' in node_data:
                    cpu_data = node_data['cpu']
                    cpu_usage_pct = cpu_data * 100 if isinstance(cpu_data, (int, float)) else 0
                    
                    system_metrics['cpu'] = {
                        'usage_percent': cpu_usage_pct
                    }
                    
                    # Send CPU metrics
                    if cpu_usage:
                        cpu_usage.set(cpu_usage_pct, node_labels)
                    
                    logger.info(f"CPU Usage: {cpu_usage_pct:.1f}%")
                
                # Uptime
                uptime_seconds = node_data.get('uptime', 0)
                if node_uptime:
                    node_uptime.set(uptime_seconds, node_labels)
                
                logger.info(f"Node Uptime: {uptime_seconds/(60*60*24):.1f} days")
                
                # Get network metrics
                try:
                    net_data = collect_network_metrics(node_labels, net_in_bytes, net_out_bytes)
                    system_metrics['network'] = net_data
                except Exception as e:
                    logger.error(f"Error collecting network metrics: {e}")
                
                # Get disk I/O metrics
                try:
                    io_data = collect_disk_io_metrics(node_labels, disk_io_read, disk_io_write)
                    system_metrics['disk_io'] = io_data
                except Exception as e:
                    logger.error(f"Error collecting disk I/O metrics: {e}")
                
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing node status JSON: {e}")
    
    except Exception as e:
        logger.error(f"Error collecting system metrics: {e}")
    
    return system_metrics


def collect_network_metrics(node_labels, net_in_bytes=None, net_out_bytes=None):
    """Collect network interface metrics."""
    logger.info("Collecting network metrics")
    network_metrics = {}
    
    # Run command to get network interface statistics
    net_stats = run_command("cat /proc/net/dev")
    if not net_stats:
        return network_metrics
    
    # Parse network statistics
    lines = net_stats.strip().split('\n')[2:]  # Skip header lines
    for line in lines:
        parts = line.strip().split(':')
        if len(parts) != 2:
            continue
        
        iface = parts[0].strip()
        # Skip loopback and virtual interfaces
        if iface == 'lo' or iface.startswith('vmbr') or iface.startswith('veth'):
            continue
        
        stats = parts[1].strip().split()
        if len(stats) < 16:
            continue
        
        # Extract statistics (refer to /proc/net/dev documentation for field meanings)
        rx_bytes = int(stats[0])
        rx_packets = int(stats[1])
        rx_errors = int(stats[2])
        rx_dropped = int(stats[3])
        
        tx_bytes = int(stats[8])
        tx_packets = int(stats[9])
        tx_errors = int(stats[10])
        tx_dropped = int(stats[11])
        
        # Store metrics
        network_metrics[iface] = {
            'rx_bytes': rx_bytes,
            'rx_packets': rx_packets,
            'rx_errors': rx_errors,
            'rx_dropped': rx_dropped,
            'tx_bytes': tx_bytes,
            'tx_packets': tx_packets,
            'tx_errors': tx_errors,
            'tx_dropped': tx_dropped
        }
        
        # Create interface-specific labels
        iface_labels = dict(node_labels, **{"interface": iface})
        
        # Send bytes metrics
        if net_in_bytes:
            net_in_bytes.set(rx_bytes, iface_labels)
        if net_out_bytes:
            net_out_bytes.set(tx_bytes, iface_labels)
        
        logger.info(f"Network {iface}: RX {rx_bytes/(1024**2):.1f}MB, TX {tx_bytes/(1024**2):.1f}MB")
    
    return network_metrics


def collect_disk_io_metrics(node_labels, disk_io_read=None, disk_io_write=None):
    """Collect disk I/O metrics."""
    logger.info("Collecting disk I/O metrics")
    io_metrics = {}
    
    # Run command to get disk I/O statistics
    io_stats = run_command("cat /proc/diskstats")
    if not io_stats:
        return io_metrics
    
    # Parse disk I/O statistics
    for line in io_stats.strip().split('\n'):
        parts = line.strip().split()
        if len(parts) < 14:
            continue
        
        device = parts[2]
        # Skip non-physical devices and partitions
        if device.startswith(('loop', 'ram', 'dm-')) or re.match(r'.*\d+$', device):
            continue
        
        # Extract statistics
        reads_completed = int(parts[3])
        reads_merged = int(parts[4])
        sectors_read = int(parts[5])
        time_reading_ms = int(parts[6])
        
        writes_completed = int(parts[7])
        writes_merged = int(parts[8])
        sectors_written = int(parts[9])
        time_writing_ms = int(parts[10])
        
        # Calculate bytes (sector size is typically 512 bytes)
        bytes_read = sectors_read * 512
        bytes_written = sectors_written * 512
        
        # Store metrics
        io_metrics[device] = {
            'reads_completed': reads_completed,
            'reads_merged': reads_merged,
            'bytes_read': bytes_read,
            'time_reading_ms': time_reading_ms,
            'writes_completed': writes_completed,
            'writes_merged': writes_merged,
            'bytes_written': bytes_written,
            'time_writing_ms': time_writing_ms
        }
        
        # Create device-specific labels
        device_labels = dict(node_labels, **{"device": device})
        
        # Send bytes metrics
        if disk_io_read:
            disk_io_read.set(bytes_read, device_labels)
        if disk_io_write:
            disk_io_write.set(bytes_written, device_labels)
        
        logger.info(f"Disk {device}: Read {bytes_read/(1024**3):.1f}GB, Write {bytes_written/(1024**3):.1f}GB")
    
    return io_metrics


def collect_cluster_status(cluster_quorate=None, cluster_nodes=None):
    """Collect Proxmox cluster status metrics."""
    logger.info("Collecting cluster status")
    cluster_metrics = {
        'quorate': None,
        'nodes': {},
        'in_cluster': False
    }
    
    # Check if node is part of a cluster
    cluster_status = run_command("pvesh get /cluster/status -output-format json")
    if not cluster_status:
        logger.info("Node is not part of a cluster")
        return cluster_metrics
    
    try:
        status_data = json.loads(cluster_status)
        if not status_data:
            logger.info("No cluster data available")
            return cluster_metrics
        
        # Node is part of a cluster
        cluster_metrics['in_cluster'] = True
        
        # Get quorum status
        for item in status_data:
            if item.get('type') == 'quorum':
                quorate = item.get('quorate', 0)
                cluster_metrics['quorate'] = bool(quorate)
                
                # Send quorate metric
                if cluster_quorate:
                    cluster_quorate.set(1 if quorate else 0, {})
                
                logger.info(f"Cluster quorate: {quorate}")
                
            elif item.get('type') == 'node':
                node_id = item.get('name', 'unknown')
                node_online = item.get('online', 0)
                
                # Store node status
                cluster_metrics['nodes'][node_id] = {
                    'online': bool(node_online),
                    'ip': item.get('ip', 'unknown'),
                    'id': item.get('id', 0)
                }
                
                # Send node online metric
                if cluster_nodes:
                    cluster_nodes.set(1 if node_online else 0, {"node": node_id})
                
                logger.info(f"Cluster node {node_id}: {'online' if node_online else 'offline'}")
    
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing cluster status JSON: {e}")
    
    return cluster_metrics