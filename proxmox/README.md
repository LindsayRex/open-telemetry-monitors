# Proxmox OpenTelemetry Monitor

A comprehensive monitoring solution for Proxmox VE servers using OpenTelemetry.

## Features

- **System Metrics**: CPU, memory, disk I/O, and network usage
- **VM Statistics**: Status, CPU usage, and memory consumption for each VM
- **Storage Monitoring**: Usage statistics and SMART disk health data
- **Temperature Monitoring**: CPU core, NVMe drives, and other system temperatures
- **Log Collection**: System logs and journal entries

## Prerequisites

- Proxmox VE 7.0+
- Python 3.10+
- OpenTelemetry backend (like Grafana LGTM stack)

## Installation

1. Clone this repository on your Proxmox server:
   ```bash
   git clone https://github.com/yourusername/open-telemetry-monitors.git
   cd open-telemetry-monitors/proxmox
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. Configure the monitor by editing `lib/config.py` with your OpenTelemetry endpoint details.

4. Install as a service:
   ```bash
   sudo cp proxmox-otel-monitor.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable proxmox-otel-monitor.service
   sudo systemctl start proxmox-otel-monitor.service
   ```

## Configuration

Edit `lib/config.py` to set the following options:

- `OTEL_METRICS_ENDPOINT`: URL of your OpenTelemetry metrics endpoint
- `OTEL_LOGS_ENDPOINT`: URL of your OpenTelemetry logs endpoint
- `COLLECTION_INTERVAL_SECONDS`: How often to collect metrics (default: 60)
- `LOG_COLLECTION_INTERVAL_SECONDS`: How often to collect logs (default: 300)

## Docker LGTM Stack (Optional)

For an easy OpenTelemetry backend setup, you can use the Grafana LGTM stack (Loki, Grafana, Tempo, Mimir).

Example docker-compose.yml:
```yaml
version: '3'

services:
  otel-lgtm:
    image: grafana/otel-lgtm:latest
    container_name: otel-lgtm
    ports:
      - "3000:3000"  # Grafana UI
      - "4317:4317"  # OpenTelemetry gRPC endpoint
      - "4318:4318"  # OpenTelemetry HTTP endpoint
    volumes:
      - grafana-data:/var/lib/grafana
      - prometheus-data:/var/lib/prometheus
      - loki-data:/var/lib/loki
      - tempo-data:/var/lib/tempo
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    restart: unless-stopped

volumes:
  grafana-data:
  prometheus-data:
  loki-data:
  tempo-data:
```

## Architecture

The monitor is organized into modular collectors:

- `system_collector.py`: CPU, memory, network, and disk I/O metrics
- `vm_collector.py`: Virtual machine statistics
- `storage_collector.py`: Storage pool usage and SMART data
- `temperature_collector.py`: Temperature monitoring from multiple sensors

## License

MIT