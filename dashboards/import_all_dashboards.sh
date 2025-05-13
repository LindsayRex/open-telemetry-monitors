#!/bin/bash
# Import all dashboards in /opt/open-telemetry-monitors/dashboards into Grafana
# Requires GRAFANA_API_TOKEN in secrets.env and Grafana at http://192.168.0.185:3000

set -e
cd "$(dirname "$0")"

source ../secrets.env

GRAFANA_URL="http://192.168.0.185:3000"
DASHBOARDS=(overview node_details vm_dashboard storage_disk temperature_sensors logs_alerts)

for DASH in "${DASHBOARDS[@]}"; do
  FILE="${DASH}.json"
  echo "Importing $FILE..."
  curl -sS -X POST "$GRAFANA_URL/api/dashboards/db" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $GRAFANA_API_TOKEN" \
    -d @$FILE \
    || { echo "Failed to import $FILE"; exit 1; }
done

echo "All dashboards imported successfully."
