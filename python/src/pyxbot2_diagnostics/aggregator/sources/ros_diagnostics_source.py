"""ROS DiagnosticArray input source."""

from __future__ import annotations

from typing import Any, Callable

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage


def _level_to_int(level: Any) -> int:
    if isinstance(level, (bytes, bytearray)):
        return int(level[0]) if level else 0
    if isinstance(level, str):
        if level.isdigit():
            return int(level)
        if len(level) == 1:
            return ord(level)
        return int(level)
    return int(level)


def _stamp_to_seconds(stamp: Any, fallback: float) -> float:
    if stamp is None:
        return fallback
    sec = getattr(stamp, "sec", None)
    nanosec = getattr(stamp, "nanosec", None)
    if sec is None or nanosec is None:
        return fallback
    return float(sec) + float(nanosec) * 1e-9


class RosDiagnosticsSource:
    """Subscribe to ROS DiagnosticArray messages and expose normalized diagnostics."""

    def __init__(
        self,
        *,
        node: Any,
        diagnostic_array_type: Any,
        input_topic: str,
        spin_once: Callable[..., None],
        time_fn: Callable[[], float],
        shutdown: Callable[[], None] | None = None,
    ) -> None:
        self._node = node
        self._spin_once = spin_once
        self._time_fn = time_fn
        self._shutdown = shutdown
        self._pending: list[DiagnosticsMessage] = []
        self._subscription = node.create_subscription(
            diagnostic_array_type, input_topic, self._handle_array, 10
        )

    def _handle_array(self, array: Any) -> None:
        receive_time = self._time_fn()
        stamp = _stamp_to_seconds(getattr(getattr(array, "header", None), "stamp", None), receive_time)

        for status in getattr(array, "status", []):
            values = tuple(
                DiagnosticKeyValue(key=str(kv.key), value=str(kv.value))
                for kv in getattr(status, "values", [])
            )
            self._pending.append(
                DiagnosticsMessage(
                    v=1,
                    node=str(status.name),
                    hw_id=str(status.hardware_id),
                    stamp=stamp,
                    level=_level_to_int(status.level),
                    msg=str(status.message),
                    values=values,
                )
            )

    def poll(self, timeout_ms: int = 100) -> list[DiagnosticsMessage]:
        self._spin_once(self._node, timeout_sec=max(timeout_ms, 0) / 1000.0)
        messages = self._pending
        self._pending = []
        return messages

    def close(self) -> None:
        if hasattr(self._node, "destroy_subscription"):
            self._node.destroy_subscription(self._subscription)
        if hasattr(self._node, "destroy_node"):
            self._node.destroy_node()
        if self._shutdown is not None:
            self._shutdown()
