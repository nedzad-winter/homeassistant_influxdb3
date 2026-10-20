#!/bin/bash
set -e

CONFIG_PATH=/data/options.json
NODE_ID=$(jq -r '.node_id // "home-assistant"' "$CONFIG_PATH")
CLUSTER_ID=$(jq -r '.cluster_id // "ha-cluster"' "$CONFIG_PATH")
LICENSE_EMAIL=$(jq -r '.license_email // ""' "$CONFIG_PATH")
LICENSE_TYPE=$(jq -r '.license_type // "home"' "$CONFIG_PATH")
SNAPSHOT_MB=$(jq -r '.force_snapshot_mem_size_mb // 512' "$CONFIG_PATH")
EXEC_POOL_MB=$(jq -r '.exec_mem_pool_size_mb // 256' "$CONFIG_PATH")
FILE_CACHE_MB=$(jq -r '.file_cache_size_mb // 2048' "$CONFIG_PATH")
REPLICA_BUFFER_MB=$(jq -r '.replica_max_buffer_size_mb // 512' "$CONFIG_PATH")
GEN1_DURATION=$(jq -r '.gen1_duration // "10m"' "$CONFIG_PATH")
QUERY_FILE_LIMIT=$(jq -r '.query_file_limit // 10000' "$CONFIG_PATH")
COMPACTION_MAX_FILES=$(jq -r '.compaction_max_num_files_per_plan // 2000' "$CONFIG_PATH")
SKIP_FILE_INDEX=$(jq -r 'if .compacted_data_skip_file_index == null then true else .compacted_data_skip_file_index end' "$CONFIG_PATH")
UPGRADE_PACHA_TREE=$(jq -r 'if .upgrade_pacha_tree == null then false else .upgrade_pacha_tree end' "$CONFIG_PATH")
# Fallbacks match the config.yaml defaults: Supervisor only learns a new schema key after
# the store refreshes, so until then options.json has no such key and the jq default is
# what actually decides. Booleans use a null check because jq's // also replaces false.
DATA_DIR=/data/influxdb3
TOKEN_FILE=/data/admin_token.json

mkdir -p "$DATA_DIR"

if [ -z "$LICENSE_EMAIL" ]; then
  echo "ERROR: license_email is empty. InfluxDB 3 Enterprise needs an email address to"
  echo "issue its licence (the free Home tier is non-commercial, max 2 cores, single node)."
  exit 1
fi

# Enterprise refuses to start if these match, and the catalog lives under the
# CLUSTER id ("<data-dir>/<cluster-id>/catalog") whereas the node's own data (wal,
# snapshots, dbs) stays under the NODE id. Core wrote everything under the node id,
# so migrating means copying only <node-id>/catalog to <cluster-id>/catalog and
# keeping node_id unchanged - the Home licence allows just one node per cluster,
# so starting under a new node id fails licence validation outright.
if [ "$CLUSTER_ID" = "$NODE_ID" ]; then
  echo "ERROR: cluster_id and node_id must differ (cluster_id=${CLUSTER_ID})."
  exit 1
fi

echo "==================================================="
echo "InfluxDB 3 Enterprise Add-on starting (cluster-id: ${CLUSTER_ID}, node-id: ${NODE_ID})"
echo "Licence: type=${LICENSE_TYPE}"
echo "Memory bounds: force-snapshot=${SNAPSHOT_MB}mb exec-pool=${EXEC_POOL_MB}mb file-cache=${FILE_CACHE_MB}mb replica-buffer=${REPLICA_BUFFER_MB}mb gen1-duration=${GEN1_DURATION} query-file-limit=${QUERY_FILE_LIMIT}"
echo "Compaction: max-num-files-per-plan=${COMPACTION_MAX_FILES} skip-file-index=${SKIP_FILE_INDEX}"
echo "Storage engine upgrade (--upgrade-pacha-tree): ${UPGRADE_PACHA_TREE}"
echo "==================================================="

SERVER_PID=""
PROXY_PID=""

# Supervisor sends SIGTERM and SIGKILLs shortly after. Without forwarding it,
# influxdb3 never gets the signal, is killed outright (exit 137) and loses whatever
# is still buffered in the WAL. Forward it and wait for a real shutdown instead.
term_handler() {
  echo "SIGTERM received - shutting down cleanly..."
  if [ -n "$PROXY_PID" ]; then
    kill -TERM "$PROXY_PID" 2>/dev/null || true
  fi
  if [ -n "$SERVER_PID" ]; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    # Let influxdb3 flush its WAL and deregister the node before we exit.
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  echo "shutdown complete"
  exit 0
}
trap term_handler SIGTERM SIGINT

# --compacted-data-skip-file-index is a bare boolean flag, so it has to be added
# conditionally rather than passed a value. It loads compacted data WITHOUT building
# the in-memory file index: query pruning gets worse, memory drops a lot. Needed here
# because ~2.5M orphaned gen1 files (never deleted after compaction - upstream
# influxdata/influxdb#27301) make that index enormous.
SKIP_INDEX_ARG=()
if [ "$SKIP_FILE_INDEX" = "true" ]; then
  SKIP_INDEX_ARG=(--compacted-data-skip-file-index)
fi

# One-time Parquet -> PachaTree migration. The server keeps serving reads and writes while
# it runs. It reports "complete" even when jobs failed, so verify per-table counts
# against a baseline before ever running cleanup-parquet.
UPGRADE_ARG=()
if [ "$UPGRADE_PACHA_TREE" = "true" ]; then
  echo "WARNING: upgrade_pacha_tree is on - migrating this cluster to the PachaTree engine."
  echo "         Turn the option off again once the upgrade coordinator reports the cluster upgrade complete."
  UPGRADE_ARG=(--upgrade-pacha-tree)
fi

# A hard power loss can leave the newest WAL file at 0 bytes. Seen after one: influxdb3
# logged "Skipping corrupt WAL file" for it on every start, then exited 1 with "node ...
# still running in another process" until the empty file was moved away, which put the
# Supervisor watchdog into a restart loop. Quarantine empty WAL files only - a non-empty
# one is never touched. They go OUTSIDE the WAL directory: 3.12's WAL follower logs a
# warning several times a second for any foreign file left in there (as *.wal.bad was).
WAL_DIR="$DATA_DIR/$NODE_ID/wal"
WAL_QUARANTINE=/data/wal-quarantine  # outside the object store, so no engine sweep ever lists it
if [ -d "$WAL_DIR" ]; then
  find "$WAL_DIR" -maxdepth 1 -type f \( -name '*.wal' -size 0 -o -name '*.wal.bad' \) | while read -r BAD_WAL; do
    mkdir -p "$WAL_QUARANTINE"
    echo "WARNING: quarantining WAL file $(basename "$BAD_WAL") (zero-byte from an unclean shutdown, or an old .bad) to ${WAL_QUARANTINE}"
    mv -- "$BAD_WAL" "$WAL_QUARANTINE/" || true
  done
fi

# force-snapshot-mem-size and exec-mem-pool-size default to PERCENTAGES OF HOST
# RAM (50% / 20%), which is unsafe on a shared HA host - always pass absolutes.
# compaction-max-num-files-per-plan defaults to 500, which silently skips any
# window holding more gen1 files than that ("skipping level compaction due to file
# limit"), leaving historical windows fragmented forever.
influxdb3 serve \
  --node-id="$NODE_ID" \
  --cluster-id="$CLUSTER_ID" \
  --license-email="$LICENSE_EMAIL" \
  --license-type="$LICENSE_TYPE" \
  --object-store=file \
  --data-dir="$DATA_DIR" \
  --http-bind=0.0.0.0:8181 \
  --force-snapshot-mem-size="${SNAPSHOT_MB}mb" \
  --exec-mem-pool-size="${EXEC_POOL_MB}mb" \
  --file-cache-size="${FILE_CACHE_MB}mb" \
  --replica-max-buffer-size="${REPLICA_BUFFER_MB}mb" \
  --gen1-duration="$GEN1_DURATION" \
  --query-file-limit="$QUERY_FILE_LIMIT" \
  --compaction-max-num-files-per-plan="$COMPACTION_MAX_FILES" \
  "${SKIP_INDEX_ARG[@]}" \
  "${UPGRADE_ARG[@]}" &
SERVER_PID=$!
START_TS=$(date +%s)

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

set +e
wait -n $SERVER_PID $PROXY_PID
EXIT_CODE=$?
set -e

# The Supervisor watchdog restarts a failed add-on immediately. A start that dies within
# seconds (WAL replay at ~120% CPU each time) then loops every ~17s and starves Home
# Assistant on this shared host. Hold the failure for a while first; "sleep & wait" so the
# SIGTERM trap still fires straight away instead of after the sleep.
CRASH_BACKOFF_SECS=120
RUNTIME=$(( $(date +%s) - START_TS ))
if [ "$EXIT_CODE" -ne 0 ] && [ "$RUNTIME" -lt 60 ]; then
  echo "ERROR: exited with code ${EXIT_CODE} after ${RUNTIME}s - waiting ${CRASH_BACKOFF_SECS}s before exiting so the watchdog does not restart-loop"
  sleep "$CRASH_BACKOFF_SECS" &
  wait $!
fi
exit "$EXIT_CODE"
