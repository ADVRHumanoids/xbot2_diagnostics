"""InfluxDB sink for diagnostics, health snapshots, and fault events."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage

LOGGER = logging.getLogger(__name__)
_FLUSH_INTERVAL_SEC = 1.0


class InfluxDBSink:
    """Write diagnostics to InfluxDB v2.

    Ordinary diagnostics keep the existing path-derived measurement schema.

    Health messages (paths ending in ``/health``) are written as one periodic
    ``health`` snapshot per source. Fault transitions are written separately as
    ``fault_event`` points. Standardized ``fault_report`` values are tags so
    Grafana can filter and group by report efficiently.
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
        self._last_fault_by_source: dict[tuple[str, str], Any] = {}

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
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        LOGGER.info("InfluxDB sink enabled: url=%s bucket=%s org=%s", url, bucket, org)

    def handle_message(self, message: DiagnosticsMessage) -> None:
        if not self._enabled or self._write_api is None:
            return

        path = message.node
        parts = [part for part in path.split("/") if part]
        measurement = parts[-1] if parts else "unknown"
        name = parts[-2] if len(parts) >= 2 else measurement
        component = "/".join(parts[:-2])
        tags = {
            "hw_id": message.hw_id if message.hw_id else "unknown",
            "path": path,
            "name": name,
            "component": component,
        }

        if measurement.lower() == "health":
            fields = self._health_fields(message)
            if fields is None:
                # Preserve ordinary diagnostics export while avoiding a
                # misleading lifecycle snapshot for malformed health data.
                fields = self._generic_fields(message)
        else:
            fields = self._generic_fields(message)

        self._pending.append(
            {
                "measurement": measurement,
                "tags": tags,
                "fields": fields,
                "time": self._timestamp_ns(message.stamp),
            }
        )

    def handle_fault_transitions(self, transitions, states) -> None:
        del states
        if not self._enabled or self._write_api is None:
            return

        for transition in transitions:
            state = transition.state
            source = (state.key.hw_id, state.key.node)
            self._last_fault_by_source[source] = state

            parts = [part for part in state.key.node.split("/") if part]
            name = parts[-2] if len(parts) >= 2 else "health"
            component = "/".join(parts[:-2])
            fields: dict[str, Any] = {
                "active": state.active,
                "level": state.level,
                "message": state.message,
                "occurrence_count": state.occurrence_count,
                "first_raised_ns": int(1e9 * state.first_raised),
                "last_raised_ns": int(1e9 * state.last_raised),
            }
            if state.last_cleared is not None:
                fields["last_cleared_ns"] = int(1e9 * state.last_cleared)

            self._pending.append(
                {
                    "measurement": "fault_event",
                    "tags": {
                        "hw_id": state.key.hw_id,
                        "path": state.key.node,
                        "name": name,
                        "component": component,
                        "fault_report": state.key.report,
                        "transition": transition.kind,
                    },
                    "fields": fields,
                    "time": int(1e9 * transition.stamp),
                }
            )

    def _health_fields(self, message: DiagnosticsMessage) -> dict[str, Any] | None:
        reports: list[str] = []
        counts: list[int] = []
        fields: dict[str, Any] = {"level": message.level}

        for kv in message.values:
            key = kv.key.strip().lower()
            if key == "fault_report":
                report = str(kv.value).strip()
                if report:
                    reports.append(report)
            elif key == "fault_count":
                try:
                    counts.append(int(str(kv.value).strip(), 10))
                except ValueError:
                    return None
            else:
                self._add_generic_field(fields, kv.key, kv.value)

        if len(counts) != 1 or counts[0] != len(set(reports)):
            return None

        fields["fault_count"] = counts[0]
        fields["active_fault_reports"] = "; ".join(sorted(set(reports)))
        if message.msg:
            fields["message"] = message.msg

        source = (message.hw_id if message.hw_id else "unknown", message.node)
        last_fault = self._last_fault_by_source.get(source)
        if last_fault is not None:
            fields["last_fault_report"] = last_fault.key.report
            fields["last_fault_active"] = last_fault.active
            fields["last_fault_level"] = last_fault.level
            fields["last_raised_ns"] = int(1e9 * last_fault.last_raised)
            if last_fault.last_cleared is not None:
                fields["last_cleared_ns"] = int(1e9 * last_fault.last_cleared)

        return fields

    def _generic_fields(self, message: DiagnosticsMessage) -> dict[str, Any]:
        fields: dict[str, Any] = {"level": message.level}
        for kv in message.values:
            self._add_generic_field(fields, kv.key, kv.value)
        if message.msg:
            fields["message"] = message.msg
        return fields

    @staticmethod
    def _add_generic_field(fields: dict[str, Any], key: str, value: Any) -> None:
        try:
            fields[key] = float(value)
        except (ValueError, TypeError):
            fields[key] = str(value)

    @staticmethod
    def _timestamp_ns(stamp: Any) -> int:
        try:
            value = float(stamp)
        except (TypeError, ValueError):
            value = time.time()
        if not math.isfinite(value) or value <= 0.0:
            value = time.time()
        return int(1e9 * value)

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
        if self._pending and self._enabled and self._write_api is not None:
            try:
                self._write_api.write(bucket=self._bucket, org=self._org, record=self._pending)
            except Exception as exc:
                LOGGER.warning("InfluxDB final flush failed: %s", exc)
        if self._client is not None:
            self._client.close()
