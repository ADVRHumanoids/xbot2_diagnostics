import json
import os
import time
from collections.abc import Callable

import zmq

from .stats_accumulator import StatAccumulator


class DiagPublisher:
    """ZMQ PUSH publisher mirroring the C++ DiagPublisher.

    Endpoint resolution order:
      1. explicit ``endpoint`` argument
      2. ``XBOT_DIAG_ENDPOINT`` environment variable
      3. ``tcp://localhost:9268`` (default)
    """

    def __init__(
        self,
        node_name: str,
        hw_id: str,
        endpoint: str = "",
        ctx: zmq.Context | None = None,
        throttle_publish_interval_sec: float = 0.0,
        *,
        time_fn: Callable[[], float] = time.time,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not endpoint:
            endpoint = os.environ.get("XBOT_DIAG_ENDPOINT", "tcp://localhost:9268")
        self._owns_ctx = ctx is None
        self._ctx = ctx if ctx is not None else zmq.Context()
        self._socket: zmq.Socket = self._ctx.socket(zmq.PUSH)
        self._socket.connect(endpoint)
        self._node   = node_name
        self._hw_id  = hw_id
        self._throttle_publish_interval_sec = throttle_publish_interval_sec
        self._time_fn = time_fn
        self._monotonic_fn = monotonic_fn
        self._last_publish_time: float | None = None

    # ------------------------------------------------------------------ #

    def _should_publish(self) -> bool:
        now = self._monotonic_fn()
        if (
            self._throttle_publish_interval_sec > 0.0
            and self._last_publish_time is not None
            and now - self._last_publish_time < self._throttle_publish_interval_sec
        ):
            return False
        self._last_publish_time = now
        return True

    def _send(
        self,
        level: int,
        msg: str,
        values: dict[str, float] | None = None,
    ) -> None:
        values = values or {}
        payload = {
            "v":      1,
            "node":   self._node,
            "hw_id":  self._hw_id,
            "stamp":  self._time_fn(),
            "level":  level,
            "msg":    msg,
            "values": [[k, v] for k, v in values.items()],
        }
        self._socket.send_string(json.dumps(payload), zmq.NOBLOCK)

    def close(self) -> None:
        self._socket.close(linger=0)
        if self._owns_ctx:
            self._ctx.term()

    def publish(
        self,
        level: int,
        msg: str,
        values: dict[str, float] | None = None,
    ) -> None:
        """Send a diagnostics message (non-blocking)."""
        if not self._should_publish():
            return
        self._send(level, msg, values)

    def publish_stats(
        self,
        metric_name: str,
        acc: StatAccumulator,
        level: int = 0,
        msg: str = "OK",
    ) -> None:
        """Flush *acc* and publish all stats fields."""
        if not self._should_publish():
            return
        st = acc.flush()
        self._send(level, msg, {
            f"{metric_name}.mean":  st.mean,
            f"{metric_name}.std":   st.std_dev,
            f"{metric_name}.min":   st.min,
            f"{metric_name}.max":   st.max,
            f"{metric_name}.p05":   st.p05,
            f"{metric_name}.p50":   st.p50,
            f"{metric_name}.p95":   st.p95,
            f"{metric_name}.count": float(st.count),
        })
