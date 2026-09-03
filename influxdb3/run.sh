#!/bin/bash
set -e

CONFIG_PATH=/data/options.json
NODE_ID=$(jq -r '.node_id // "home-assistant"' "$CONFIG_PATH")
SNAPSHOT_MB=$(jq -r '.force_snapshot_mem_size_mb // 512' "$CONFIG_PATH")
EXEC_POOL_MB=$(jq -r '.exec_mem_pool_size_mb // 256' "$CONFIG_PATH")
FILE_CACHE_MB=$(jq -r '.file_cache_size_mb // 256' "$CONFIG_PATH")
GEN1_DURATION=$(jq -r '.gen1_duration // "10m"' "$CONFIG_PATH")
QUERY_FILE_LIMIT=$(jq -r '.query_file_limit // 10000' "$CONFIG_PATH")
DATA_DIR=/data/influxdb3
TOKEN_FILE=/data/admin_token.json

mkdir -p "$DATA_DIR"

echo "==================================================="
echo "InfluxDB 3 Core Add-on starting (node-id: ${NODE_ID})"
echo "Memory bounds: force-snapshot=${SNAPSHOT_MB}mb exec-pool=${EXEC_POOL_MB}mb file-cache=${FILE_CACHE_MB}mb gen1-duration=${GEN1_DURATION} query-file-limit=${QUERY_FILE_LIMIT}"
echo "==================================================="
# These flags default to PERCENTAGES OF HOST RAM (force-snapshot-mem-size and
# exec-mem-pool-size default to 50% / 20%!) which is unsafe on a shared HA host
# running many other apps - always pass explicit absolute values here.
# gen1-duration only accepts 1m/5m/10m (not an arbitrary duration) - it can't be
# widened to reduce file fragmentation from a multi-year historical backfill.
# query-file-limit caps how many parquet files a single query may scan; Core
# doesn't auto-compact (that's an Enterprise feature), so a low-frequency but
# multi-year backfilled dataset needs this raised or ordinary dashboard time
# ranges fail with "exceeding the file limit". Entity-filtered queries (what
# every real dashboard panel does) touch far fewer files than an unfiltered
# full-table scan, so this mainly protects against accidental full scans.

influxdb3 serve \
  --node-id="$NODE_ID" \
  --object-store=file \
  --data-dir="$DATA_DIR" \
  --http-bind=0.0.0.0:8181 \
  --force-snapshot-mem-size="${SNAPSHOT_MB}mb" \
  --exec-mem-pool-size="${EXEC_POOL_MB}mb" \
  --file-cache-size="${FILE_CACHE_MB}mb" \
  --gen1-duration="$GEN1_DURATION" \
  --query-file-limit="$QUERY_FILE_LIMIT" &
SERVER_PID=$!

if [ ! -f "$TOKEN_FILE" ]; then
  echo "First boot detected: waiting for the server to accept connections so an admin token can be created..."
  for i in $(seq 1 60); do
    # Note: the influxdb3 CLI exits 0 and writes connection errors to stdout,
    # so we must check the actual JSON content, not the exit code.
    influxdb3 create token --admin --format json --host http://127.0.0.1:8181 > "$TOKEN_FILE" 2>/tmp/token_err.log || true
    if jq -e '.token' "$TOKEN_FILE" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if [ -s "$TOKEN_FILE" ] && jq -e '.token' "$TOKEN_FILE" >/dev/null 2>&1; then
    echo "==================================================="
    echo "Admin token created and saved to ${TOKEN_FILE} inside the add-on's persistent /data."
    echo "Copy it now - it will not be shown in full again:"
    jq -r '.token' "$TOKEN_FILE"
    echo "==================================================="
  else
    echo "ERROR: failed to create admin token on first boot. Last error:"
    cat /tmp/token_err.log 2>/dev/null || true
    rm -f "$TOKEN_FILE"
  fi
else
  echo "Existing admin token file found at ${TOKEN_FILE}; not creating a new one."
fi

echo "Starting HA-compatibility proxy on :8080 (translates empty test-writes to 204, like InfluxDB 1.x)"
python3 /compat_proxy.py &
PROXY_PID=$!

wait -n $SERVER_PID $PROXY_PID
