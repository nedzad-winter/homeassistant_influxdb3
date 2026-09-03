#!/bin/bash
set -e

# Read configuration from Home Assistant add-on options
CONFIG_PATH=/data/options.json
INTERVAL_HOURS=$(jq -r '.interval_hours // 1' $CONFIG_PATH)
INTERVAL_SECONDS=$((INTERVAL_HOURS * 3600))

echo "==================================================="
echo "Mod Download Counter Add-on Started"
echo "Interval: Every ${INTERVAL_HOURS} hour(s)"
echo "==================================================="

# Main loop
while true; do
    echo ""
    echo "[$(date)] Running mod download counter..."
    python3 /app/mod_download_counter.py || echo "[ERROR] Script failed, will retry next interval"
    
    echo ""
    echo "[$(date)] Sleeping for ${INTERVAL_HOURS} hour(s)..."
    sleep $INTERVAL_SECONDS
done
