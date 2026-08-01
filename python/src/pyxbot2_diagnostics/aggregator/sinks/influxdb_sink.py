"""InfluxDB sink for diagnostics metric values."""

from __future__ import annotations

import logging
import time
from typing import Any

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage
from pyxbot2_diagnostics.aggregator.health import (
    HealthMessageValidationError,
    HealthStatus,
    is_health_message,
    parse_health_message,
    timestamp_seconds_to_ns,
)

LOGGER = logging.getLogger(__name__)

# Generic measurement name for ordinary robot diagnostics.
_MEASUREMENT = "robot_diagnostics"
_DEVICE_HEALTH_MEASUREMENT = "device_health"
_FAULT_COUNTER_MEASUREMENT = "fault_counter"

# Minimum seconds between batch writes to InfluxDB.
_FLUSH_INTERVAL_SEC = 1.0


class InfluxDBSink:
    """Write diagnostics to InfluxDB v2.

    Ordinary diagnostics retain the existing schema. Messages whose path ends in
    ``/health`` or ``/health_status`` are validated against the device-health
    contract and normalized into two measurements:

    ``device_health``
        tags: hw_id, path, device_path, schema, schema_version, boot_id
        fields: level, active_fault_count, message, plus non-contract scalar values

    ``fault_counter``
        one point per fault code
        tags: hw_id, path, device_path, fault_code, schema_version, boot_id
        fields: active, raise_count_total, last_raised_ns, last_cleared_ns

    Invalid health messages are logged and omitted from InfluxDB rather than being
    written as generic diagnostics with opaque JSON fields.
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

        if is_health_message(message):
            self._handle_health_message(message)
            return

        self._pending.append(self._generic_point(message))

    def _handle_health_message(self, message: DiagnosticsMessage) -> None:
        try:
            health = parse_health_message(message)
        except HealthMessageValidationError as exc:
            LOGGER.warning("Rejecting invalid health message %s: %s", message.node, exc)
            return

        sample_time_ns = timestamp_seconds_to_ns(message.stamp)
        self._pending.append(self._device_health_point(message, health, sample_time_ns))
        self._pending.extend(
            self._fault_counter_points(message, health, sample_time_ns)
        )

    @staticmethod
    def _generic_point(message: DiagnosticsMessage) -> dict[str, Any]:
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

        """
        node schema is defined as follows:
        <component...>/<name>/<measurement>

        example: /xbot/joint/knee_pitch_1/pos_ref -->
          component = /xbot/joint
          name = knee_pitch_1
          measurement = pos_ref
        """

        measurement = parts[-1] if parts else "unknown"
        name = parts[-2] if len(parts) >= 2 else measurement
        component = "/".join(parts[:-2])

        return {
            "measurement": measurement,
            "tags": {
                "hw_id": message.hw_id if message.hw_id else "unknown",
                "path": path,
                "name": name,
                "component": component,
            },
            "fields": fields,
            "time": int(1e9 * time.time()),
        }

    @staticmethod
    def _device_health_point(
        message: DiagnosticsMessage,
        health: HealthStatus,
        sample_time_ns: int,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "level": message.level,
            "active_fault_count": health.active_fault_count,
        }
        if message.msg:
            fields["message"] = message.msg

        # Preserve optional health metadata when it is scalar. Structured optional
        # values remain JSON strings to avoid dynamic nested InfluxDB schemas.
        for entry in health.extra_values:
            fields[entry.key] = _coerce_health_field(entry.value)

        return {
            "measurement": _DEVICE_HEALTH_MEASUREMENT,
            "tags": {
                "hw_id": message.hw_id,
                "path": message.node,
                "device_path": health.device_path,
                "schema": health.schema_name,
                "schema_version": str(health.schema_version),
                "boot_id": health.boot_id,
            },
            "fields": fields,
            "time": sample_time_ns,
        }

    @staticmethod
    def _fault_counter_points(
        message: DiagnosticsMessage,
        health: HealthStatus,
        sample_time_ns: int,
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        for fault in health.faults:
            fields: dict[str, Any] = {
                "active": fault.active,
                "raise_count_total": fault.raise_count_total,
            }
            if fault.last_raised_ns is not None:
                fields["last_raised_ns"] = fault.last_raised_ns
            if fault.last_cleared_ns is not None:
                fields["last_cleared_ns"] = fault.last_cleared_ns

            points.append(
                {
                    "measurement": _FAULT_COUNTER_MEASUREMENT,
                    "tags": {
                        "hw_id": message.hw_id,
                        "path": message.node,
                        "device_path": health.device_path,
                        "fault_code": fault.code,
                        "schema_version": str(health.schema_version),
                        "boot_id": health.boot_id,
                    },
                    "fields": fields,
                    "time": sample_time_ns,
                }
            )
        return points

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


def _coerce_health_field(value: Any) -> Any:
    if isinstance(value, (bool, int, float, str)):
        return value
    try:
        import json

        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return str(value)
