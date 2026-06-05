"""Stdout diagnostics sink."""

from __future__ import annotations

import json
import time
from typing import Callable

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticsMessage


class StdoutSink:
    """Pretty-print latest diagnostics state on a fixed interval."""

    def __init__(self, interval_sec: float, time_fn: Callable[[], float] = time.time) -> None:
        self._interval_sec = interval_sec
        self._time_fn = time_fn
        self._last_print = 0.0

    def handle_message(self, message: DiagnosticsMessage) -> None:
        del message

    def publish_state(self, states: dict[str, DiagnosticsMessage]) -> None:
        now = self._time_fn()
        if self._last_print and (now - self._last_print) < self._interval_sec:
            return
        self._last_print = now
        payload = {
            node: {
                "level": msg.level,
                "msg": msg.msg,
                "hw_id": msg.hw_id,
                "stamp": msg.stamp,
                "values": [{"key": kv.key, "value": kv.value} for kv in msg.values],
            }
            for node, msg in sorted(states.items())
        }
        print(json.dumps(payload, indent=2, sort_keys=True))

    def close(self) -> None:
        return
