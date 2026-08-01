"""Validation and normalization for device health diagnostics."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage

HEALTH_SCHEMA_NAME = "xbot.device_health"
HEALTH_SCHEMA_VERSION = 1
HEALTH_STATUS_SUFFIXES = frozenset({"health", "health_status"})

_SCHEMA_NAME_KEY = "schema.name"
_SCHEMA_VERSION_KEY = "schema.version"
_BOOT_ID_KEY = "device.boot_id"
_ACTIVE_KEY = "faults.active"
_RAISE_COUNT_KEY = "faults.raise_count_total"
_LAST_RAISED_KEY = "faults.last_raised"
_LAST_CLEARED_KEY = "faults.last_cleared"

REQUIRED_HEALTH_KEYS = frozenset(
    {
        _SCHEMA_NAME_KEY,
        _SCHEMA_VERSION_KEY,
        _BOOT_ID_KEY,
        _ACTIVE_KEY,
        _RAISE_COUNT_KEY,
        _LAST_RAISED_KEY,
        _LAST_CLEARED_KEY,
    }
)


class HealthMessageValidationError(ValueError):
    """Raised when a health diagnostic does not satisfy the health contract."""


@dataclass(frozen=True)
class FaultHealthRecord:
    """Normalized state for one fault code."""

    code: str
    active: bool
    raise_count_total: int
    last_raised_ns: int | None
    last_cleared_ns: int | None


@dataclass(frozen=True)
class HealthStatus:
    """Normalized device health snapshot."""

    device_path: str
    schema_name: str
    schema_version: int
    boot_id: str
    faults: tuple[FaultHealthRecord, ...]
    extra_values: tuple[DiagnosticKeyValue, ...]

    @property
    def active_fault_count(self) -> int:
        return sum(fault.active for fault in self.faults)


def is_health_message(message: DiagnosticsMessage) -> bool:
    """Return whether *message* is identified as a device health status."""

    parts = _path_parts(message.node)
    return bool(parts) and parts[-1] in HEALTH_STATUS_SUFFIXES


def parse_health_message(message: DiagnosticsMessage) -> HealthStatus:
    """Validate and normalize a ``/health`` or ``/health_status`` message."""

    parts = _path_parts(message.node)
    if not parts or parts[-1] not in HEALTH_STATUS_SUFFIXES:
        raise HealthMessageValidationError(
            "health status name must end with '/health' or '/health_status'"
        )
    if len(parts) < 2:
        raise HealthMessageValidationError("health status name must include a device path")
    if not message.hw_id.strip():
        raise HealthMessageValidationError("health status requires a non-empty hardware_id")
    if not math.isfinite(message.stamp) or message.stamp < 0:
        raise HealthMessageValidationError("health status stamp must be finite and non-negative")

    values = _unique_value_map(message.values)
    missing = sorted(REQUIRED_HEALTH_KEYS - values.keys())
    if missing:
        raise HealthMessageValidationError(
            "health status is missing required keys: " + ", ".join(missing)
        )

    schema_name = _require_non_empty_string(values[_SCHEMA_NAME_KEY], _SCHEMA_NAME_KEY)
    if schema_name != HEALTH_SCHEMA_NAME:
        raise HealthMessageValidationError(
            f"{_SCHEMA_NAME_KEY} must be '{HEALTH_SCHEMA_NAME}', got '{schema_name}'"
        )

    schema_version = _parse_schema_version(values[_SCHEMA_VERSION_KEY])
    if schema_version != HEALTH_SCHEMA_VERSION:
        raise HealthMessageValidationError(
            f"unsupported health schema version {schema_version}; "
            f"expected {HEALTH_SCHEMA_VERSION}"
        )

    boot_id = _require_non_empty_string(values[_BOOT_ID_KEY], _BOOT_ID_KEY)
    active_codes = _parse_active_faults(values[_ACTIVE_KEY])
    counts = _parse_counter_map(values[_RAISE_COUNT_KEY])
    last_raised = _parse_timestamp_map(values[_LAST_RAISED_KEY], _LAST_RAISED_KEY)
    last_cleared = _parse_timestamp_map(values[_LAST_CLEARED_KEY], _LAST_CLEARED_KEY)

    referenced_codes = set(active_codes) | set(last_raised) | set(last_cleared)
    unknown_codes = sorted(referenced_codes - counts.keys())
    if unknown_codes:
        raise HealthMessageValidationError(
            f"{_RAISE_COUNT_KEY} is missing referenced fault codes: "
            + ", ".join(unknown_codes)
        )

    faults: list[FaultHealthRecord] = []
    active_set = set(active_codes)
    for code in sorted(counts):
        count = counts[code]
        raised_ns = last_raised.get(code)
        cleared_ns = last_cleared.get(code)
        active = code in active_set

        if count == 0 and raised_ns is not None:
            raise HealthMessageValidationError(
                f"fault '{code}' has zero raises but a non-null last-raised timestamp"
            )
        if count > 0 and raised_ns is None:
            raise HealthMessageValidationError(
                f"fault '{code}' has a positive raise counter but no last-raised timestamp"
            )
        if active and count == 0:
            raise HealthMessageValidationError(
                f"active fault '{code}' must have a positive raise counter"
            )
        if raised_ns is not None and cleared_ns is not None:
            if active and cleared_ns >= raised_ns:
                raise HealthMessageValidationError(
                    f"active fault '{code}' has last-cleared >= last-raised"
                )
            if not active and raised_ns > cleared_ns:
                raise HealthMessageValidationError(
                    f"inactive fault '{code}' has last-raised > last-cleared"
                )

        faults.append(
            FaultHealthRecord(
                code=code,
                active=active,
                raise_count_total=count,
                last_raised_ns=raised_ns,
                last_cleared_ns=cleared_ns,
            )
        )

    extra_values = tuple(
        entry for entry in message.values if entry.key not in REQUIRED_HEALTH_KEYS
    )
    device_path = "/" + "/".join(parts[:-1])
    return HealthStatus(
        device_path=device_path,
        schema_name=schema_name,
        schema_version=schema_version,
        boot_id=boot_id,
        faults=tuple(faults),
        extra_values=extra_values,
    )


def timestamp_seconds_to_ns(value: int | float) -> int:
    """Convert finite non-negative epoch seconds to integer nanoseconds."""

    seconds = Decimal(str(value))
    if not seconds.is_finite() or seconds < 0:
        raise HealthMessageValidationError(
            "timestamp seconds must be finite and non-negative"
        )
    return int(seconds * Decimal(1_000_000_000))


def _path_parts(path: str) -> list[str]:
    return [part for part in path.split("/") if part]


def _unique_value_map(values: Iterable[DiagnosticKeyValue]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    duplicates: list[str] = []
    for entry in values:
        if entry.key in result:
            duplicates.append(entry.key)
        else:
            result[entry.key] = entry.value
    if duplicates:
        raise HealthMessageValidationError(
            "health status contains duplicate keys: " + ", ".join(sorted(set(duplicates)))
        )
    return result


def _require_non_empty_string(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HealthMessageValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _parse_schema_version(value: Any) -> int:
    if isinstance(value, bool):
        raise HealthMessageValidationError(f"{_SCHEMA_VERSION_KEY} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise HealthMessageValidationError(
                f"{_SCHEMA_VERSION_KEY} must be an integer"
            ) from exc
    raise HealthMessageValidationError(f"{_SCHEMA_VERSION_KEY} must be an integer")


def _decode_json(value: Any, key: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise HealthMessageValidationError(f"{key} contains invalid JSON: {exc.msg}") from exc
    return value


def _parse_active_faults(value: Any) -> tuple[str, ...]:
    decoded = _decode_json(value, _ACTIVE_KEY)
    if not isinstance(decoded, list):
        raise HealthMessageValidationError(f"{_ACTIVE_KEY} must be a JSON array")

    result: list[str] = []
    for index, code in enumerate(decoded):
        if not isinstance(code, str) or not code.strip():
            raise HealthMessageValidationError(
                f"{_ACTIVE_KEY}[{index}] must be a non-empty string"
            )
        result.append(code.strip())

    if len(result) != len(set(result)):
        raise HealthMessageValidationError(f"{_ACTIVE_KEY} must not contain duplicates")
    return tuple(result)


def _parse_counter_map(value: Any) -> dict[str, int]:
    decoded = _decode_json(value, _RAISE_COUNT_KEY)
    if not isinstance(decoded, dict):
        raise HealthMessageValidationError(f"{_RAISE_COUNT_KEY} must be a JSON object")

    result: dict[str, int] = {}
    for raw_code, count in decoded.items():
        code = _validate_fault_code(raw_code, _RAISE_COUNT_KEY)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise HealthMessageValidationError(
                f"{_RAISE_COUNT_KEY}['{code}'] must be a non-negative integer"
            )
        result[code] = count
    return result


def _parse_timestamp_map(value: Any, key: str) -> dict[str, int | None]:
    decoded = _decode_json(value, key)
    if not isinstance(decoded, dict):
        raise HealthMessageValidationError(f"{key} must be a JSON object")

    result: dict[str, int | None] = {}
    for raw_code, timestamp in decoded.items():
        code = _validate_fault_code(raw_code, key)
        result[code] = _parse_timestamp(timestamp, f"{key}['{code}']")
    return result


def _validate_fault_code(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HealthMessageValidationError(f"{key} fault codes must be non-empty strings")
    return value.strip()


def _parse_timestamp(value: Any, location: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HealthMessageValidationError(
            f"{location} must be null, epoch seconds, or an ISO-8601 timestamp"
        )
    if isinstance(value, (int, float)):
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise HealthMessageValidationError(
                f"{location} epoch seconds must be finite and non-negative"
            )
        return timestamp_seconds_to_ns(seconds)
    if not isinstance(value, str) or not value.strip():
        raise HealthMessageValidationError(
            f"{location} must be null, epoch seconds, or an ISO-8601 timestamp"
        )

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HealthMessageValidationError(
            f"{location} must be a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HealthMessageValidationError(f"{location} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )
