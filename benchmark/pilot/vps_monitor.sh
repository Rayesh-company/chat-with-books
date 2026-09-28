#!/usr/bin/env bash
# The pilot's resource sampler: one SSH session on the VPS (port 6041)
# that samples `docker stats` for the chat-with-books containers plus
# the host's load and free memory every SAMPLE seconds, as CSV on
# stdout. Redirect to the runs directory; the report correlates the
# window with the ask/research timestamps.
#
# Usage:
#   ./vps_monitor.sh <minutes> <out.csv> [sample-seconds]
set -euo pipefail

MINUTES="${1:-10}"
OUT="${2:-vps-stats.csv}"
SAMPLE="${3:-5}"

HOST="${VPS_HOST:-ubuntu@94.183.176.80}"
PORT="${VPS_PORT:-6041}"

# The sample count the loop walks; one sample per SAMPLE seconds.
COUNT=$(( MINUTES * 60 / SAMPLE ))

ssh -p "$PORT" "$HOST" bash -s > "$OUT" <<EOF
echo "ts,container,cpu_pct,mem_usage,mem_pct,net_io,block_io"
for i in \$(seq 1 $COUNT); do
  ts=\$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}}' 2>/dev/null \
    | grep '^chat-with-books' | sed "s/^/\$ts,/"
  read -r a b c _ < /proc/loadavg
  mem_avail=\$(awk '/MemAvailable/ {printf "%.0f", \$2/1024}' /proc/meminfo)
  echo "\$ts,HOST,loadavg=\$a/\$b/\$c,mem_avail_mb=\$mem_avail,,,"
  sleep $SAMPLE
done
EOF

echo "saved: $OUT"
