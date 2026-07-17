#!/usr/bin/env bash
# influx_clear_bucket.bash — delete all data from a bucket (or a single measurement).
#
# Usage:
#   ./influx_clear_bucket.bash [--measurement MEAS] [--url URL] [--org ORG] [--bucket BUCKET]
#
# Without --measurement, ALL data in the bucket is deleted.

set -euo pipefail

URL="${INFLUXDB_URL:-http://localhost:8086}"
TOKEN="${INFLUXDB_TOKEN:-xbot2-diagnostics-dev-token}"
ORG="${INFLUXDB_ORG:-xbot2}"
BUCKET="${INFLUXDB_BUCKET:-diagnostics}"
MEASUREMENT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --measurement) MEASUREMENT="$2"; shift 2 ;;
        --url)         URL="$2";         shift 2 ;;
        --org)         ORG="$2";         shift 2 ;;
        --bucket)      BUCKET="$2";      shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -n "$MEASUREMENT" ]]; then
    PREDICATE=", \"predicate\": \"_measurement=\\\"$MEASUREMENT\\\"\""
    echo "Deleting measurement '$MEASUREMENT' from bucket '$BUCKET' (org=$ORG) ..."
else
    PREDICATE=""
    echo "Deleting ALL data from bucket '$BUCKET' (org=$ORG) ..."
fi

HTTP=$(curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "$URL/api/v2/delete?org=$ORG&bucket=$BUCKET" \
    -H "Authorization: Token $TOKEN" \
    -H "Content-Type: application/json" \
    --data-binary "{\"start\": \"1970-01-01T00:00:00Z\", \"stop\": \"2099-01-01T00:00:00Z\"$PREDICATE}")

if [[ "$HTTP" == "204" ]]; then
    echo "Done (HTTP 204)."
else
    echo "Failed (HTTP $HTTP)." >&2
    exit 1
fi