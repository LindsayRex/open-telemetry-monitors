#!/usr/bin/env python3
"""
ZFS pool metrics collector for Proxmox OpenTelemetry Monitoring
"""
import json
import re
from lib.config import logger
from lib.utils import run_command

def collect_zfs_pool_metrics(health_status=None, capacity_ratio=None, frag_ratio=None, 
                            checksum_errors=None, read_bytes=None, write_bytes=None, 
                            read_ops=None, write_ops=None):
    """Collect ZFS pool metrics including health, capacity, fragmentation, and I/O statistics."""
    logger.info("Collecting ZFS pool metrics")
    zfs_metrics = {}
    
    # Get list of ZFS pools
    pools_output = run_command("zpool list -H -o name")
    if not pools_output:
        logger.error("Failed to get ZFS pool list")
        return zfs_metrics
    
    pools = pools_output.strip().split('\n')
    for pool in pools:
        pool = pool.strip()
        if not pool:
            continue
        
        logger.info(f"Processing ZFS pool: {pool}")
        zfs_metrics[pool] = {
            'health': 'UNKNOWN',
            'capacity': 0,
            'fragmentation': 0,
            'checksum_errors': 0,
            'read_bytes': 0,
            'write_bytes': 0,
            'read_ops': 0,
            'write_ops': 0
        }
        
        # Get health status, capacity, and fragmentation in a single command
        pool_info_cmd = f"zpool list -H -o health,capacity,fragmentation {pool}"
        pool_info = run_command(pool_info_cmd)
        
        if pool_info:
            parts = pool_info.strip().split('\t')
            if len(parts) >= 3:
                # 1. Health status
                health = parts[0].strip()
                zfs_metrics[pool]['health'] = health
                
                # Map health status to numeric value for gauge
                health_value = 0  # ONLINE
                if health == "DEGRADED":
                    health_value = 1
                elif health == "FAULTED":
                    health_value = 2
                elif health == "OFFLINE":
                    health_value = 3
                elif health == "UNAVAIL":
                    health_value = 4
                elif health == "REMOVED":
                    health_value = 5
                
                # Send health metric (ObservableGauge - use set())
                if health_status:
                    health_status.set(health_value, {"pool": pool, "health_text": health})
                    
                logger.info(f"ZFS pool {pool} health status: {health}")
                
                # 2. Capacity
                capacity_str = parts[1].strip()
                capacity_match = re.search(r'(\d+)%', capacity_str)
                if capacity_match:
                    capacity = float(capacity_match.group(1))
                    zfs_metrics[pool]['capacity'] = capacity
                    
                    # Send capacity metric (ObservableGauge - use set())
                    if capacity_ratio:
                        capacity_ratio.set(capacity, {"pool": pool})
                        
                    logger.info(f"ZFS pool {pool} capacity: {capacity}%")
                
                # 3. Fragmentation
                frag_str = parts[2].strip()
                frag_match = re.search(r'(\d+)%', frag_str)
                if frag_match:
                    fragmentation = float(frag_match.group(1))
                    zfs_metrics[pool]['fragmentation'] = fragmentation
                    
                    # Send fragmentation metric (ObservableGauge - use set())
                    if frag_ratio:
                        frag_ratio.set(fragmentation, {"pool": pool})
                        
                    logger.info(f"ZFS pool {pool} fragmentation: {fragmentation}%")
        else:
            logger.error(f"Failed to get pool info for {pool} using command: {pool_info_cmd}")
        
        # 4. Get checksum errors
        cksum_output = run_command(f"zpool status {pool} | grep CKSUM | awk '{{print $5}}' | grep -v '-'")
        if cksum_output:
            # Sum up all checksum errors in the pool
            total_cksum = 0
            for line in cksum_output.strip().split('\n'):
                try:
                    if line.strip():
                        total_cksum += int(line.strip())
                except ValueError:
                    continue
            
            zfs_metrics[pool]['checksum_errors'] = total_cksum
            
            # Send checksum errors metric (ObservableCounter - use observe())
            if checksum_errors:
                checksum_errors.observe(total_cksum, {"pool": pool})
                
            logger.info(f"ZFS pool {pool} checksum errors: {total_cksum}")
        
        # 5. Get I/O statistics (using -Hp for parsable output with raw values)
        io_cmd = f"zpool iostat -Hp {pool} 1 1"
        io_output = run_command(io_cmd)
        if io_output:
            lines = io_output.strip().split('\n')
            if lines:
                parts = lines[0].strip().split('\t')  # -Hp uses tabs
                if len(parts) >= 7 and parts[0] == pool:
                    try:
                        # With -Hp flag, the output columns are:
                        # pool_name alloc_bytes free_bytes read_ops write_ops read_bytes write_bytes
                        read_ops_val = int(parts[3])
                        write_ops_val = int(parts[4])
                        read_bytes_val = int(parts[5])
                        write_bytes_val = int(parts[6])
                        
                        zfs_metrics[pool]['read_ops'] = read_ops_val
                        zfs_metrics[pool]['write_ops'] = write_ops_val
                        zfs_metrics[pool]['read_bytes'] = read_bytes_val
                        zfs_metrics[pool]['write_bytes'] = write_bytes_val
                        
                        # Send I/O metrics (ObservableCounter - use observe())
                        if read_ops:
                            read_ops.observe(read_ops_val, {"pool": pool})
                        if write_ops:
                            write_ops.observe(write_ops_val, {"pool": pool})
                        if read_bytes:
                            read_bytes.observe(read_bytes_val, {"pool": pool})
                        if write_bytes:
                            write_bytes.observe(write_bytes_val, {"pool": pool})
                            
                        logger.info(f"ZFS pool {pool} I/O: {read_bytes_val} bytes read, {write_bytes_val} bytes written, {read_ops_val} read ops, {write_ops_val} write ops")
                    except (ValueError, IndexError) as e:
                        logger.error(f"Error parsing ZFS I/O statistics for pool {pool} from '{io_cmd}': {e}, output: '{io_output}'")
                else:
                    logger.error(f"Unexpected output format from '{io_cmd}' for pool {pool}: '{io_output}'")
            else:
                logger.error(f"No output from '{io_cmd}' for pool {pool}")
        else:
            logger.error(f"Failed to get I/O stats for {pool} using command: {io_cmd}")
    
    return zfs_metrics

def _convert_to_bytes(size_str):
    """Convert size string (like '1.2K', '3M', '5G') to bytes."""
    try:
        if 'K' in size_str:
            return float(size_str.replace('K', '')) * 1024
        elif 'M' in size_str:
            return float(size_str.replace('M', '')) * 1024 * 1024
        elif 'G' in size_str:
            return float(size_str.replace('G', '')) * 1024 * 1024 * 1024
        elif 'T' in size_str:
            return float(size_str.replace('T', '')) * 1024 * 1024 * 1024 * 1024
        else:
            return float(size_str)
    except ValueError:
        return 0