import json
import os
import time

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
        ctx: zmq.Context,
        node_name: str,
        hw_id: str,
        endpoint: str = "",
    ) -> None:
        if not endpoint:
            endpoint = os.environ.get("XBOT_DIAG_ENDPOINT", "tcp://localhost:9268")
        self._socket: zmq.Socket = ctx.socket(zmq.PUSH)
        self._socket.connect(endpoint)
        self._node   = node_name
        self._hw_id  = hw_id

    # ------------------------------------------------------------------ #

    def publish(
        self,
        level: int,
        msg: str,
        values: dict[str, float] | None = None,
    ) -> None:
        """Send a diagnostics message (non-blocking)."""
        payload = {
            "v":      1,
            "node":   self._node,
            "hw_id":  self._hw_id,
            "stamp":  time.time(),
            "level":  level,
            "msg":    msg,
            "values": [[k, v] for k, v in (values or {}).items()],
        }
        self._socket.send_string(json.dumps(payload), zmq.NOBLOCK)

    def publish_stats(
        self,
        metric_name: str,
        acc: StatAccumulator,
        level: int = 0,
        msg: str = "OK",
    ) -> None:
        """Flush *acc* and publish all stats fields."""
        st = acc.flush()
        self.publish(level, msg, {
            f"{metric_name}.mean":  st.mean,
            f"{metric_name}.std":   st.std_dev,
            f"{metric_name}.min":   st.min,
            f"{metric_name}.max":   st.max,
            f"{metric_name}.p05":   st.p05,
            f"{metric_name}.p50":   st.p50,
            f"{metric_name}.p95":   st.p95,
            f"{metric_name}.count": float(st.count),
        })
