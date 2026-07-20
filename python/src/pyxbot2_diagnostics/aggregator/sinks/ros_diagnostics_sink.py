"""ROS DiagnosticArray sink."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage


try:
    from diagnostic_msgs.msg import DiagnosticArray as RosDiagnosticArray
    from diagnostic_msgs.msg import DiagnosticStatus as RosDiagnosticStatus
    from diagnostic_msgs.msg import KeyValue as RosKeyValue
except ImportError:  # pragma: no cover - exercised with fallback types in tests
    @dataclass
    class RosKeyValue:
        key: str = ""
        value: str = ""

    @dataclass
    class RosDiagnosticStatus:
        level: int = 0
        name: str = ""
        message: str = ""
        hardware_id: str = ""
        values: list[RosKeyValue] = field(default_factory=list)

    @dataclass
    class _Header:
        stamp: float = 0.0

    @dataclass
    class RosDiagnosticArray:
        header: _Header = field(default_factory=_Header)
        status: list[RosDiagnosticStatus] = field(default_factory=list)


_ROS_LEVEL_USES_BYTES = isinstance(RosDiagnosticStatus().level, bytes)


class RosDiagnosticsSink:
    """Publish path-aggregated diagnostics as DiagnosticArray messages."""

    def __init__(
        self,
        aggregated_publisher: Callable[[RosDiagnosticArray], None],
        time_fn: Callable[[], float],
        stamp_fn: Callable[[], Any] | None = None,
        *,
        aggregation_root: str = "Robot",
        publish_rate_hz: float = 1.0,
        rate_time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if publish_rate_hz <= 0:
            raise ValueError("publish_rate_hz must be > 0")
        self._aggregated_publisher = aggregated_publisher
        self._time_fn = time_fn
        self._stamp_fn = stamp_fn
        self._aggregation_root = aggregation_root.strip("/") or "Robot"
        self._publish_period_sec = 1.0 / publish_rate_hz
        self._rate_time_fn = rate_time_fn
        self._last_publish_time: float | None = None

    @staticmethod
    def _to_kv(kv: DiagnosticKeyValue) -> RosKeyValue:
        return RosKeyValue(key=kv.key, value=str(kv.value))

    @staticmethod
    def _to_level(level: int) -> int | bytes:
        if _ROS_LEVEL_USES_BYTES:
            return bytes([level])
        return level

    @classmethod
    def _to_status(cls, msg: DiagnosticsMessage, *, name: str | None = None) -> RosDiagnosticStatus:
        return RosDiagnosticStatus(
            level=cls._to_level(msg.level),
            name=msg.node if name is None else name,
            message=msg.msg,
            hardware_id=msg.hw_id,
            values=[cls._to_kv(kv) for kv in msg.values],
        )

    @staticmethod
    def _level_message(level: int) -> str:
        return {
            0: "OK",
            1: "WARN",
            2: "ERROR",
            3: "STALE",
        }.get(level, "UNKNOWN")

    @classmethod
    def _to_group_status(cls, name: str, level: int) -> RosDiagnosticStatus:
        return RosDiagnosticStatus(
            level=cls._to_level(level),
            name=name,
            message=cls._level_message(level),
            hardware_id="",
            values=[],
        )

    def _new_array(self) -> RosDiagnosticArray:
        array = RosDiagnosticArray()
        if hasattr(array, "header") and hasattr(array.header, "stamp"):
            array.header.stamp = self._stamp_fn() if self._stamp_fn is not None else self._time_fn()
        return array

    def _aggregate_segments(self, name: str) -> list[str]:
        segments = [segment for segment in name.strip("/").split("/") if segment]
        if segments and segments[0] == self._aggregation_root:
            segments = segments[1:]
        return [self._aggregation_root, *segments]

    @staticmethod
    def _aggregate_path(segments: list[str], length: int) -> str:
        return "/" + "/".join(segments[:length])

    def _build_aggregated_statuses(
        self, states: dict[str, DiagnosticsMessage]
    ) -> list[RosDiagnosticStatus]:
        group_levels: dict[str, int] = {}
        leaf_statuses: dict[str, RosDiagnosticStatus] = {}

        for _, msg in sorted(states.items()):
            segments = self._aggregate_segments(msg.node)
            leaf_path = self._aggregate_path(segments, len(segments))
            leaf_statuses[leaf_path] = self._to_status(msg, name=leaf_path)
            for length in range(1, len(segments) + 1):
                path = self._aggregate_path(segments, length)
                group_levels[path] = max(group_levels.get(path, 0), msg.level)

        statuses: list[RosDiagnosticStatus] = []
        for path in sorted(group_levels):
            statuses.append(leaf_statuses.get(path) or self._to_group_status(path, group_levels[path]))
        return statuses

    def handle_message(self, message: DiagnosticsMessage) -> None:
        del message

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        now = self._rate_time_fn()
        if (
            self._last_publish_time is not None
            and (now - self._last_publish_time) < self._publish_period_sec
        ):
            return
        self._last_publish_time = now
        aggregated_array = self._new_array()
        aggregated_array.status = self._build_aggregated_statuses(states)
        self._aggregated_publisher(aggregated_array)

    def close(self) -> None:
        return
