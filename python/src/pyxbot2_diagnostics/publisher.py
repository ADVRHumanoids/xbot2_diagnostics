from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import zmq

from .stats_accumulator import StatAccumulator

LOGGER = logging.getLogger(__name__)


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
        send_hwm: int | None = None,
        immediate: bool = False,
    ) -> None:
        if not endpoint:
            endpoint = os.environ.get("XBOT_DIAG_ENDPOINT", "tcp://localhost:9268")
        if send_hwm is not None and send_hwm <= 0:
            raise ValueError("send_hwm must be > 0")
        self._owns_ctx = ctx is None
        self._ctx = ctx if ctx is not None else zmq.Context()
        self._socket: zmq.Socket = self._ctx.socket(zmq.PUSH)
        if send_hwm is not None:
            self._socket.setsockopt(zmq.SNDHWM, send_hwm)
        if immediate:
            self._socket.setsockopt(zmq.IMMEDIATE, 1)
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
        return True

    def _send(
        self,
        level: int,
        msg: str,
        values: dict[str, Any] | None = None,
    ) -> bool:
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
        try:
            self._socket.send_string(json.dumps(payload, allow_nan=False), zmq.NOBLOCK)
        except zmq.Again:
            LOGGER.debug("Dropping diagnostics sample for %s: ZMQ sender unavailable", self._node)
            return False
        return True

    def close(self) -> None:
        self._socket.close(linger=0)
        if self._owns_ctx:
            self._ctx.term()

    def publish(
        self,
        level: int,
        msg: str,
        values: dict[str, Any] | None = None,
    ) -> bool:
        """Send a diagnostics message (non-blocking)."""
        if not self._should_publish():
            return False
        sent = self._send(level, msg, values)
        if sent:
            self._last_publish_time = self._monotonic_fn()
        return sent

    def publish_stats(
        self,
        metric_name: str,
        acc: StatAccumulator,
        level: int = 0,
        msg: str | None = None,
    ) -> bool:
        """Flush *acc* and publish all stats fields."""
        if not self._should_publish():
            return False
        st = acc.flush()
        if msg is None:
            msg = f"{{mean: {st.mean:.3f}, std: {st.std_dev:.3f}, min: {st.min:.3f}, max: {st.max:.3f}, count:{st.count}}}"
        sent = self._send(level, msg, {
            f"{metric_name}.mean":  st.mean,
            f"{metric_name}.std":   st.std_dev,
            f"{metric_name}.min":   st.min,
            f"{metric_name}.max":   st.max,
            f"{metric_name}.p05":   st.p05,
            f"{metric_name}.p50":   st.p50,
            f"{metric_name}.p95":   st.p95,
            f"{metric_name}.count": float(st.count),
        })
        if sent:
            self._last_publish_time = self._monotonic_fn()
        return sent
