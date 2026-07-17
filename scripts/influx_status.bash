#!/usr/bin/env bash
# influx_status.bash — check InfluxDB connectivity, bucket contents, and schema.
#
# Usage:
#   ./influx_status.bash [--url URL] [--org ORG] [--bucket BUCKET] [--window WINDOW]
#
# Defaults come from environment variables:
#   INFLUXDB_TOKEN   (falls back to xbot2-diagnostics-dev-token)
#   INFLUXDB_URL     (falls back to http://localhost:8086)
#   INFLUXDB_ORG     (falls back to xbot2)
#   INFLUXDB_BUCKET  (falls back to diagnostics)
#
# WINDOW is a Flux duration string, e.g. 1h (default), 30m, 7d.

set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
URL="${INFLUXDB_URL:-http://localhost:8086}"
TOKEN="${INFLUXDB_TOKEN:-xbot2-diagnostics-dev-token}"
ORG="${INFLUXDB_ORG:-xbot2}"
BUCKET="${INFLUXDB_BUCKET:-diagnostics}"
WINDOW="1h"

# ── argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)    URL="$2";    shift 2 ;;
        --org)    ORG="$2";    shift 2 ;;
        --bucket) BUCKET="$2"; shift 2 ;;
        --window) WINDOW="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── helpers ───────────────────────────────────────────────────────────────────
CURL_OPTS=(-sS -H "Authorization: Token $TOKEN")
WRITE_URL="$URL/api/v2/write?org=$ORG&bucket=$BUCKET&precision=ns"
QUERY_URL="$URL/api/v2/query?org=$ORG"

flux_query() {
    curl "${CURL_OPTS[@]}" -X POST "$QUERY_URL" \
        -H "Content-type: application/vnd.flux" \
        -H "Accept: application/csv" \
        --data-binary "$1" \
        | grep -v '^#\|^,result\|^$' | cut -d, -f4
}

header() { printf "\n\033[1;36m=== %s ===\033[0m\n" "$*"; }
ok()     { printf "  \033[32m✓\033[0m  %s\n" "$*"; }
fail()   { printf "  \033[31m✗\033[0m  %s\n" "$*"; }
info()   { printf "  ·  %s\n" "$*"; }

# ── 1. connectivity ───────────────────────────────────────────────────────────
header "Connectivity  ($URL)"
HTTP_PING=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" "$URL/ping" 2>/dev/null || echo "000")
if [[ "$HTTP_PING" == "204" ]]; then
    ok "InfluxDB is reachable (HTTP $HTTP_PING)"
else
    fail "InfluxDB unreachable — got HTTP $HTTP_PING"
    exit 1
fi

# ── 2. token / org ────────────────────────────────────────────────────────────
header "Auth  (org=$ORG)"
HTTP_AUTH=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" \
    "$URL/api/v2/orgs?org=$ORG" 2>/dev/null || echo "000")
if [[ "$HTTP_AUTH" == "200" ]]; then
    ok "Token is valid for org '$ORG'"
else
    fail "Token check failed (HTTP $HTTP_AUTH)"
fi

# ── 3. probe write ────────────────────────────────────────────────────────────
header "Write probe  (bucket=$BUCKET)"
TS=$(date +%s%N)
HTTP_WRITE=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" \
    -X POST "$WRITE_URL" \
    -H "Content-Type: text/plain; charset=utf-8" \
    --data-binary "influx_status_probe,script=influx_status.bash value=1i $TS" \
    2>/dev/null || echo "000")
if [[ "$HTTP_WRITE" == "204" ]]; then
    ok "Probe write accepted (HTTP 204)"
else
    fail "Probe write rejected (HTTP $HTTP_WRITE)"
fi

# ── 4. measurements ───────────────────────────────────────────────────────────
header "Measurements in bucket '$BUCKET'"
MEASUREMENTS=$(flux_query '
import "influxdata/influxdb/schema"
schema.measurements(bucket: "'"$BUCKET"'")' || true)
if [[ -z "$MEASUREMENTS" ]]; then
    fail "No measurements found — bucket is empty"
else
    while IFS= read -r m; do info "$m"; done <<< "$MEASUREMENTS"
fi

# ── 5. per-measurement: tag keys, field keys, last point ──────────────────────
while IFS= read -r MEAS; do
    [[ -z "$MEAS" ]] && continue
    header "Measurement: $MEAS  (window=-$WINDOW)"

    TAG_KEYS=$(flux_query '
import "influxdata/influxdb/schema"
schema.tagKeys(bucket: "'"$BUCKET"'",
  predicate: (r) => r._measurement == "'"$MEAS"'",
  start: -'"$WINDOW"'
)' | grep -vE '^_' || true)

    FIELD_KEYS=$(flux_query '
import "influxdata/influxdb/schema"
schema.fieldKeys(bucket: "'"$BUCKET"'",
  predicate: (r) => r._measurement == "'"$MEAS"'",
  start: -'"$WINDOW"'
)' || true)

    LAST_TIME=$(flux_query '
from(bucket: "'"$BUCKET"'")
  |> range(start: -'"$WINDOW"')
  |> filter(fn: (r) => r._measurement == "'"$MEAS"'")
  |> keep(columns: ["_time"])
  |> last()' 2>/dev/null | head -n1 || true)

    if [[ -n "$TAG_KEYS" ]]; then
        info "tags:   $(echo "$TAG_KEYS" | tr '\n' '  ')"
    else
        fail "no tag keys found in window -$WINDOW"
    fi

    if [[ -n "$FIELD_KEYS" ]]; then
        info "fields: $(echo "$FIELD_KEYS" | tr '\n' '  ')"
    else
        fail "no field keys found in window -$WINDOW"
    fi

    if [[ -n "$LAST_TIME" ]]; then
        ok "last point: $LAST_TIME"
    else
        fail "no data in window -$WINDOW (try --window 7d)"
    fi
done <<< "$MEASUREMENTS"

echo
