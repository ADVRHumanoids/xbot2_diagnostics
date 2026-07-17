import json

import pytest
import zmq

from pyxbot2_diagnostics.publisher import DiagPublisher
from pyxbot2_diagnostics.stats_accumulator import StatAccumulator


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _recv_json(socket: zmq.Socket) -> dict:
    assert socket.poll(100)
    return json.loads(socket.recv_string(zmq.NOBLOCK))


def _assert_no_message(socket: zmq.Socket) -> None:
    assert socket.poll(20) == 0


def test_diag_publisher_throttles_publish() -> None:
    ctx = zmq.Context()
    pull = ctx.socket(zmq.PULL)
    endpoint = "inproc://test_diag_publisher_throttles_publish"
    pull.bind(endpoint)
    clock = FakeClock(10.0)

    pub = DiagPublisher(
        "test_node",
        "test_hw",
        endpoint,
        ctx,
        throttle_publish_interval_sec=1.0,
        time_fn=lambda: 123.0,
        monotonic_fn=clock,
    )

    pub.publish(0, "first", {"metric": 1.0})
    pub.publish(0, "throttled", {"metric": 2.0})
    assert _recv_json(pull)["msg"] == "first"
    _assert_no_message(pull)

    clock.advance(1.1)
    pub.publish(0, "second", {"metric": 3.0})
    assert _recv_json(pull)["msg"] == "second"

    pub.close()
    pull.close(linger=0)
    ctx.term()


def test_diag_publisher_does_not_flush_stats_when_throttled() -> None:
    ctx = zmq.Context()
    pull = ctx.socket(zmq.PULL)
    endpoint = "inproc://test_diag_publisher_does_not_flush_stats_when_throttled"
    pull.bind(endpoint)
    clock = FakeClock(20.0)

    pub = DiagPublisher(
        "test_node",
        "test_hw",
        endpoint,
        ctx,
        throttle_publish_interval_sec=1.0,
        time_fn=lambda: 456.0,
        monotonic_fn=clock,
    )
    acc = StatAccumulator()

    acc.update(1.0)
    pub.publish_stats("metric", acc)
    assert _recv_json(pull)["values"][-1] == ["metric.count", 1.0]

    acc.update(2.0)
    pub.publish_stats("metric", acc)
    _assert_no_message(pull)

    acc.update(4.0)
    clock.advance(1.1)
    pub.publish_stats("metric", acc)
    values = dict(_recv_json(pull)["values"])
    assert values["metric.count"] == 2.0

    pub.close()
    pull.close(linger=0)
    ctx.term()


def test_diag_publisher_drops_when_immediate_peer_is_unavailable() -> None:
    pub = DiagPublisher(
        "offline",
        "host",
        "tcp://127.0.0.1:59998",
        send_hwm=1,
        immediate=True,
    )
    assert pub.publish(0, "OK", {"metric": 1.0}) is False
    pub.close()


def test_diag_publisher_rejects_invalid_high_water_mark() -> None:
    with pytest.raises(ValueError):
        DiagPublisher("node", "host", send_hwm=0)
