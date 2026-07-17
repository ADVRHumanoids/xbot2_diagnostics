#!/usr/bin/env bash
# influx_sink_probe.bash — end-to-end diagnostic pipeline health check.
#
# Checks the full chain from ROS aggregator process → InfluxDB sink → bucket:
#   1. InfluxDB reachable
#   2. Aggregator process running
#   3. /diagnostics_agg topic publishing
#   4. robot_diagnostics measurement receiving fresh data
#   5. Recent tag-value samples
#
# Usage:
#   ./influx_sink_probe.bash [--window WINDOW]
#
# WINDOW is a Flux duration, e.g. 2m (default), 5m, 1h.

set -euo pipefail

URL="${INFLUXDB_URL:-http://localhost:8086}"
TOKEN="${INFLUXDB_TOKEN:-xbot2-diagnostics-dev-token}"
ORG="${INFLUXDB_ORG:-xbot2}"
BUCKET="${INFLUXDB_BUCKET:-diagnostics}"
MEASUREMENT="robot_diagnostics"
WINDOW="2m"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --window) WINDOW="$2"; shift 2 ;;
        --url)    URL="$2";    shift 2 ;;
        --org)    ORG="$2";    shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

CURL_OPTS=(-sS -H "Authorization: Token $TOKEN")
QUERY_URL="$URL/api/v2/query?org=$ORG"

ok()   { printf "  \033[32m[OK ]\033[0m  %s\n" "$*"; }
fail() { printf "  \033[31m[FAIL]\033[0m %s\n" "$*"; FAILED=1; }
warn() { printf "  \033[33m[WARN]\033[0m %s\n" "$*"; }
info() { printf "        %s\n" "$*"; }
hdr()  { printf "\n\033[1m%s\033[0m\n" "$*"; }

FAILED=0

# ── 1. InfluxDB reachable ─────────────────────────────────────────────────────
hdr "1. InfluxDB connectivity ($URL)"
PING=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" "$URL/ping" 2>/dev/null || echo "000")
if [[ "$PING" == "204" ]]; then ok "reachable"; else fail "not reachable (HTTP $PING)"; fi

AUTH=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" \
    "$URL/api/v2/orgs?org=$ORG" 2>/dev/null || echo "000")
if [[ "$AUTH" == "200" ]]; then ok "token valid for org '$ORG'"; else fail "token rejected (HTTP $AUTH)"; fi

# ── 2. aggregator process ─────────────────────────────────────────────────────
hdr "2. Aggregator process"
AGG_PROCS=$(pgrep -fa 'aggregator_node' 2>/dev/null || true)
if [[ -n "$AGG_PROCS" ]]; then
    ok "aggregator_node running"
    while IFS= read -r line; do info "$line"; done <<< "$AGG_PROCS"
else
    fail "no aggregator_node process found"
    info "Start it with:  ros2 launch xbot2_diagnostics xbot2_diagnostics_aggregator.launch.py"
fi

# ── 3. ROS topic /diagnostics_agg ────────────────────────────────────────────
hdr "3. ROS /diagnostics_agg topic"
if command -v ros2 &>/dev/null; then
    # Count publishers in 2s
    PUB_COUNT=$(timeout 3 ros2 topic info /diagnostics_agg 2>/dev/null \
        | grep "Publisher count:" | awk '{print $NF}' || echo "0")
    if [[ "${PUB_COUNT:-0}" -gt 0 ]]; then
        ok "$PUB_COUNT publisher(s) on /diagnostics_agg"
    else
        fail "no publishers on /diagnostics_agg"
    fi

    # Count statuses in one message
    STATUS_COUNT=$(timeout 5 ros2 topic echo /diagnostics_agg --once 2>/dev/null \
        | grep -c '^- level' || echo "0")
    if [[ "$STATUS_COUNT" -gt 0 ]]; then
        ok "$STATUS_COUNT diagnostic statuses in latest message"
    else
        warn "could not read a message (topic might be slow)"
    fi
else
    warn "ros2 not on PATH — skipping topic checks"
fi

# ── 4. robot_diagnostics measurement freshness ────────────────────────────────
hdr "4. InfluxDB measurement '$MEASUREMENT' (window=-$WINDOW)"

POINT_COUNT=$(curl "${CURL_OPTS[@]}" -X POST "$QUERY_URL" \
    -H "Content-type: application/vnd.flux" -H "Accept: application/csv" \
    --data-binary "
from(bucket: \"$BUCKET\")
  |> range(start: -$WINDOW)
  |> filter(fn: (r) => r._measurement == \"$MEASUREMENT\")
  |> count()
  |> sum(column: \"_value\")
" 2>/dev/null | grep -v '^#\|^,result\|^$' | cut -d, -f4 | head -n1 || echo "0")

if [[ "${POINT_COUNT:-0}" -gt 0 ]]; then
    ok "$POINT_COUNT field-values written in last $WINDOW"
else
    fail "no data in last $WINDOW — sink may not be writing"
    info "Check aggregator log for '[InfluxDB sink enabled' and '[InfluxDB: wrote' messages"
    info "Try a longer window: ./influx_sink_probe.bash --window 1h"
fi

LAST_TS=$(curl "${CURL_OPTS[@]}" -X POST "$QUERY_URL" \
    -H "Content-type: application/vnd.flux" -H "Accept: application/csv" \
    --data-binary "
from(bucket: \"$BUCKET\")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == \"$MEASUREMENT\")
  |> keep(columns: [\"_time\"])
  |> last()
" 2>/dev/null | grep -v '^#\|^,result\|^$' | cut -d, -f4 | head -n1 || true)

if [[ -n "$LAST_TS" ]]; then
    ok "most recent point: $LAST_TS"
else
    fail "no data at all in bucket (all time)"
fi

# ── 5. sample tag values ──────────────────────────────────────────────────────
hdr "5. Tag value samples (window=-$WINDOW)"

for TAG in hw_id name; do
    VALS=$(curl "${CURL_OPTS[@]}" -X POST "$QUERY_URL" \
        -H "Content-type: application/vnd.flux" -H "Accept: application/csv" \
        --data-binary "
import \"influxdata/influxdb/schema\"
schema.tagValues(bucket: \"$BUCKET\", tag: \"$TAG\",
  predicate: (r) => r._measurement == \"$MEASUREMENT\", start: -$WINDOW)
" 2>/dev/null | grep -v '^#\|^,result\|^$' | cut -d, -f4 | tr '\n' '  ' || true)
    if [[ -n "$VALS" ]]; then
        ok "$TAG: $VALS"
    else
        warn "$TAG: no values (no data in window)"
    fi
done

# ── 6. summary ────────────────────────────────────────────────────────────────
echo
if [[ "$FAILED" -eq 0 ]]; then
    printf "\033[32mAll checks passed.\033[0m\n"
else
    printf "\033[31mSome checks failed — see above.\033[0m\n"
    exit 1
fi
