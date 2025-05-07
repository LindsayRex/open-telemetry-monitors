# OpenTelemetry Monitors

A collection of OpenTelemetry-based monitoring solutions for various platforms and systems.

## Available Monitors

### Proxmox Monitor

A comprehensive OpenTelemetry monitoring solution for Proxmox VE servers. Collects system metrics, VM statistics, storage information, and temperature data.

[Go to Proxmox Monitor →](./proxmox/)

## Overview

This repository provides ready-to-use OpenTelemetry monitoring solutions for different platforms. Each solution is self-contained in its own directory with detailed setup instructions.

## Requirements

- Python 3.10+
- OpenTelemetry backend (like the LGTM stack - Loki, Grafana, Tempo, Mimir)

## Quick Start

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/open-telemetry-monitors.git
   cd open-telemetry-monitors
   ```
   
   For production servers where you only need the current code without history:
   ```bash
   git clone --depth=1 https://github.com/yourusername/open-telemetry-monitors.git
   cd open-telemetry-monitors
   ```
   This creates a "shallow clone" with only the most recent commit, which is faster and uses less disk space.

2. Navigate to the specific monitor you want to use (e.g., `cd proxmox`)

3. Follow the README instructions in that directory

## Git on Production Servers

When using this repository on production servers:

- Git only tracks files in directories explicitly initialized as repositories
- Use `.gitignore` to exclude unnecessary files from tracking
- For minimal resource usage, create shallow clones using `--depth=1` as shown above
- Avoid running resource-intensive git operations during peak server usage

## Contributing

Contributions are welcome! If you'd like to add a new monitor or improve an existing one, please submit a pull request.

## License

MIT