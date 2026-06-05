"""ROS DiagnosticArray sink."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage


try:
    from diagnostic_msgs.msg import DiagnosticArray as RosDiagnosticArray
    from diagnostic_msgs.msg import DiagnosticStatus as RosDiagnosticStatus
    from diagnostic_msgs.msg import KeyValue as RosKeyValue
except ImportError:  # pragma: no cover - exercised with fallback types in tests
    @dataclass(slots=True)
    class RosKeyValue:
        key: str = ""
        value: str = ""

    @dataclass(slots=True)
    class RosDiagnosticStatus:
        level: int = 0
        name: str = ""
        message: str = ""
        hardware_id: str = ""
        values: list[RosKeyValue] = field(default_factory=list)

    @dataclass(slots=True)
    class _Header:
        stamp: float = 0.0

    @dataclass(slots=True)
    class RosDiagnosticArray:
        header: _Header = field(default_factory=_Header)
        status: list[RosDiagnosticStatus] = field(default_factory=list)


_ROS_LEVEL_USES_BYTES = isinstance(RosDiagnosticStatus().level, bytes)


class RosDiagnosticsSink:
    """Publish full aggregated state as DiagnosticArray at fixed rate."""

    def __init__(
        self,
        publisher: Callable[[RosDiagnosticArray], None],
        time_fn: Callable[[], float],
        stamp_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._publisher = publisher
        self._time_fn = time_fn
        self._stamp_fn = stamp_fn
        self._last_publish = 0.0

    @staticmethod
    def _to_kv(kv: DiagnosticKeyValue) -> RosKeyValue:
        return RosKeyValue(key=kv.key, value=str(kv.value))

    @staticmethod
    def _to_level(level: int) -> int | bytes:
        if _ROS_LEVEL_USES_BYTES:
            return bytes([level])
        return level

    @classmethod
    def _to_status(cls, msg: DiagnosticsMessage) -> RosDiagnosticStatus:
        return RosDiagnosticStatus(
            level=cls._to_level(msg.level),
            name=msg.node,
            message=msg.msg,
            hardware_id=msg.hw_id,
            values=[cls._to_kv(kv) for kv in msg.values],
        )

    def handle_message(self, message: DiagnosticsMessage) -> None:
        del message

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        now = self._time_fn()

        array = RosDiagnosticArray()
        if hasattr(array, "header") and hasattr(array.header, "stamp"):
            array.header.stamp = self._stamp_fn() if self._stamp_fn is not None else now
        array.status = [self._to_status(msg) for _, msg in sorted(states.items())]
        self._publisher(array)

    def close(self) -> None:
        return
