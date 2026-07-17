"""InfluxDB sink for diagnostics metric values."""

from __future__ import annotations

import logging
import time
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage

LOGGER = logging.getLogger(__name__)

# Single measurement name for all robot diagnostics.
_MEASUREMENT = "robot_diagnostics"

# Minimum seconds between batch writes to InfluxDB.
_FLUSH_INTERVAL_SEC = 1.0


class InfluxDBSink:
    """Write diagnostics to InfluxDB v2.

    Schema
    ------
    measurement : robot_diagnostics
    tags        : hw_id, path (full status name), name (last path segment)
    fields      : level (int), one float field per kv-pair in status.values,
                  message (str, only when non-empty)

    Points are buffered in handle_message and flushed as a single batch write
    at most once per _FLUSH_INTERVAL_SEC to avoid per-message HTTP overhead.
    """

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
        self._pending: list[dict[str, Any]] = []
        self._last_flush = 0.0

        if not enabled:
            return

        if write_api is not None:
            LOGGER.info("InfluxDB sink enabled (injected write_api)")
            return

        if not (url and token and org and bucket):
            LOGGER.warning("InfluxDB sink enabled but missing configuration; disabling")
            self._enabled = False
            return

        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS
        except ImportError:
            LOGGER.warning("influxdb-client not installed; disabling InfluxDB sink")
            self._enabled = False
            return

        self._client = InfluxDBClient(url=url, token=token, org=org)
        # SYNCHRONOUS so errors surface immediately rather than being silently
        # dropped by the async batch queue.
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        LOGGER.info("InfluxDB sink enabled: url=%s bucket=%s org=%s", url, bucket, org)

    def handle_message(self, message: DiagnosticsMessage) -> None:
        if not self._enabled or self._write_api is None:
            return

        path = message.node
        parts = [p for p in path.split("/") if p]

        fields: dict[str, Any] = {"level": message.level}

        # Coerce each kv-value to float; fall back to string for non-numeric ones.
        for kv in message.values:
            try:
                fields[kv.key] = float(kv.value)
            except (ValueError, TypeError):
                fields[kv.key] = str(kv.value)

        if message.msg:
            fields["message"] = message.msg

        self._pending.append(
            {
                "measurement": _MEASUREMENT,
                "tags": {
                    "hw_id": message.hw_id if message.hw_id else "unknown",
                    "path": path,
                    "name": "/".join(parts[-2:]) if parts else path,
                    "component": "/".join(parts[:-2]) if parts else path,
                },
                "fields": fields,
                "time": int(1e9 * time.time()),
            }
        )

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        del states
        self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        now = time.monotonic()
        if (now - self._last_flush) < _FLUSH_INTERVAL_SEC:
            return
        points = self._pending
        self._pending = []
        self._last_flush = now
        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=points)
            LOGGER.info("InfluxDB: wrote %d points", len(points))
        except Exception as exc:
            LOGGER.warning("InfluxDB write failed (%d points dropped): %s", len(points), exc)

    def close(self) -> None:
        # Final flush on shutdown — ignore the rate limit.
        if self._pending and self._enabled and self._write_api is not None:
            try:
                self._write_api.write(bucket=self._bucket, org=self._org, record=self._pending)
            except Exception as exc:
                LOGGER.warning("InfluxDB final flush failed: %s", exc)
        if self._client is not None:
            self._client.close()
