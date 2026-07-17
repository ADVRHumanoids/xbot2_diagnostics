#!/usr/bin/env bash
# influx_tail.bash — continuously poll InfluxDB and print new points as they arrive.
#
# Usage:
#   ./influx_tail.bash [--measurement MEAS] [--tag TAG=VALUE] [--field FIELD]
#                      [--interval SEC] [--url URL] [--org ORG] [--bucket BUCKET]
#
# Examples:
#   ./influx_tail.bash
#   ./influx_tail.bash --measurement robot_diagnostics --tag hw_id=xbot2
#   ./influx_tail.bash --measurement robot_diagnostics --field mean --interval 2

set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
URL="${INFLUXDB_URL:-http://localhost:8086}"
TOKEN="${INFLUXDB_TOKEN:-xbot2-diagnostics-dev-token}"
ORG="${INFLUXDB_ORG:-xbot2}"
BUCKET="${INFLUXDB_BUCKET:-diagnostics}"
MEASUREMENT="robot_diagnostics"
TAG_FILTER=""
FIELD_FILTER=""
INTERVAL=2

# ── argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --measurement) MEASUREMENT="$2"; shift 2 ;;
        --tag)         TAG_FILTER="$2";  shift 2 ;;
        --field)       FIELD_FILTER="$2"; shift 2 ;;
        --interval)    INTERVAL="$2";    shift 2 ;;
        --url)         URL="$2";         shift 2 ;;
        --org)         ORG="$2";         shift 2 ;;
        --bucket)      BUCKET="$2";      shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── build optional Flux filter clauses ───────────────────────────────────────
TAG_CLAUSE=""
if [[ -n "$TAG_FILTER" ]]; then
    TAG_KEY="${TAG_FILTER%%=*}"
    TAG_VAL="${TAG_FILTER#*=}"
    TAG_CLAUSE="  |> filter(fn: (r) => r[\"$TAG_KEY\"] == \"$TAG_VAL\")"
fi

FIELD_CLAUSE=""
if [[ -n "$FIELD_FILTER" ]]; then
    FIELD_CLAUSE="  |> filter(fn: (r) => r._field == \"$FIELD_FILTER\")"
fi

QUERY_URL="$URL/api/v2/query?org=$ORG"
CURL_OPTS=(-sS -H "Authorization: Token $TOKEN")

printf "Tailing \033[1m%s\033[0m in bucket \033[1m%s\033[0m (poll every %ss) — Ctrl-C to stop\n\n" \
    "$MEASUREMENT" "$BUCKET" "$INTERVAL"

LAST_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

while true; do
    NEXT_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    ROWS=$(curl "${CURL_OPTS[@]}" -X POST "$QUERY_URL" \
        -H "Content-type: application/vnd.flux" \
        -H "Accept: application/csv" \
        --data-binary "
from(bucket: \"$BUCKET\")
  |> range(start: $LAST_TS, stop: $NEXT_TS)
  |> filter(fn: (r) => r._measurement == \"$MEASUREMENT\")
$TAG_CLAUSE
$FIELD_CLAUSE
  |> keep(columns: [\"_time\",\"_field\",\"_value\",\"hw_id\",\"path\",\"name\"])
  |> sort(columns: [\"_time\"])
" 2>/dev/null | grep -v '^#\|^,result\|^$' || true)

    if [[ -n "$ROWS" ]]; then
        # Print header once
        printf "\033[90m%-30s %-30s %-20s %-10s %s\033[0m\n" \
            "time" "path" "field" "hw_id" "value"
        printf "%s\n" "$(printf '%.0s─' {1..100})"
        while IFS=',' read -r _ _ _ time value field hw_id name path _rest; do
            printf "%-30s %-30s %-20s %-10s %s\n" \
                "${time:-?}" "${path:-?}" "${field:-?}" "${hw_id:-?}" "${value:-?}"
        done <<< "$ROWS"
        echo
    else
        printf "\r\033[K[%s] waiting for new points..." "$(date +%H:%M:%S)"
    fi

    LAST_TS="$NEXT_TS"
    sleep "$INTERVAL"
done
