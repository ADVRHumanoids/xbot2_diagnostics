"""InfluxDB sink for diagnostics metric values."""

from __future__ import annotations

import logging
from typing import Any

from aggregator.aggregator import DiagnosticsMessage

LOGGER = logging.getLogger(__name__)


class InfluxDBSink:
    """Write diagnostics metrics to InfluxDB v2 as metric-stat points."""

    _KNOWN_STATS = {"mean", "std", "min", "max", "count"}

    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        token: str,
        org: str,
        bucket: str,
        write_api: Any | None = None,
    ) -> None:
        self._enabled = enabled
        self._bucket = bucket
        self._org = org
        self._client = None
        self._write_api = write_api

        if not enabled:
            return

        if write_api is not None:
            return

        if not (url and token and org and bucket):
            LOGGER.warning("InfluxDB sink enabled but missing configuration; disabling")
            self._enabled = False
            return

        try:
            from influxdb_client import InfluxDBClient
        except ImportError:
            LOGGER.warning("influxdb-client not installed; disabling InfluxDB sink")
            self._enabled = False
            return

        self._client = InfluxDBClient(url=url, token=token, org=org)
        self._write_api = self._client.write_api()

    @staticmethod
    def _parse_metric_key(key: str) -> tuple[str, str] | None:
        if "." not in key:
            return None
        metric, stat = key.rsplit(".", 1)
        if stat not in InfluxDBSink._KNOWN_STATS:
            return None
        return metric, stat

    def handle_message(self, message: DiagnosticsMessage) -> None:
        if not self._enabled or self._write_api is None:
            return

        points: list[dict[str, Any]] = []
        for kv in message.values:
            parsed = self._parse_metric_key(kv.key)
            if parsed is None or not isinstance(kv.value, (int, float)):
                continue
            metric, stat = parsed
            points.append(
                {
                    "measurement": metric,
                    "tags": {"node": message.node, "hw_id": message.hw_id, "stat": stat},
                    "fields": {"value": float(kv.value)},
                    "time": message.stamp,
                }
            )

        if points:
            self._write_api.write(bucket=self._bucket, org=self._org, record=points)

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        del states

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
