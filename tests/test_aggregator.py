from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field

import pytest
import zmq

from pyxbot2_diagnostics.aggregator.aggregator import (
    DiagnosticKeyValue,
    DiagnosticsAggregator,
    DiagnosticsMessage,
    ZmqDiagnosticsSource,
)
from pyxbot2_diagnostics.aggregator.config import (
    AggregatorConfig,
    AggregatorSection,
    SinksSection,
)


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


@dataclass
class FakeSink:
    messages: list[object] = field(default_factory=list)
    snapshots: list[dict[str, object]] = field(default_factory=list)

    def handle_message(self, message) -> None:
        self.messages.append(message)

    def publish_state(self, states) -> None:
        self.snapshots.append(dict(states))

    def close(self) -> None:
        return


class FakeSource:
    def __init__(self, polls: list[list[DiagnosticsMessage]]) -> None:
        self.polls = polls
        self.closed = False

    def poll(self, timeout_ms: int = 100) -> list[DiagnosticsMessage]:
        del timeout_ms
        if not self.polls:
            return []
        return self.polls.pop(0)

    def close(self) -> None:
        self.closed = True


def _free_tcp_endpoint() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return f"tcp://127.0.0.1:{port}"


def _config(endpoint: str) -> AggregatorConfig:
    return AggregatorConfig(
        aggregator=AggregatorSection(
            zmq_endpoint=endpoint,
            stale_timeout_sec=1.0,
            stale_check_interval_sec=0.2,
        ),
        sinks=SinksSection(),
    )


def _send(push_socket: zmq.Socket, payload: dict) -> None:
    push_socket.send_string(json.dumps(payload))


def _message(node: str, *, level: int = 0, msg: str = "OK") -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id="hw",
        stamp=1.0,
        level=level,
        msg=msg,
        values=(DiagnosticKeyValue("x", 1),),
    )


def test_state_cache_and_retrieval_with_zmq_fixture() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    sink = FakeSink()
    clock = FakeClock()
    aggr = DiagnosticsAggregator(_config(endpoint), [sink], context=ctx, time_fn=clock)

    pub = ctx.socket(zmq.PUSH)
    pub.connect(endpoint)

    payload = {
        "v": 1,
        "node": "controller/a",
        "hw_id": "arm",
        "stamp": 1.0,
        "level": 0,
        "msg": "OK",
        "values": [{"key": "freq", "value": 1000.0}],
    }

    _send(pub, payload)
    aggr.poll_once(timeout_ms=200)

    assert "controller/a" in aggr.state_cache
    assert aggr.state_cache["controller/a"].msg == "OK"
    assert sink.messages and sink.snapshots

    pub.close(linger=0)
    aggr.close()


def test_zmq_and_ros_style_sources_share_cache() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    zmq_source = ZmqDiagnosticsSource(endpoint, context=ctx)
    ros_source = FakeSource([[_message("ros/node")]])
    sink = FakeSink()
    aggr = DiagnosticsAggregator(
        _config(endpoint), [sink], sources=[zmq_source, ros_source], time_fn=FakeClock()
    )

    pub = ctx.socket(zmq.PUSH)
    pub.connect(endpoint)
    _send(
        pub,
        {
            "v": 1,
            "node": "zmq/node",
            "hw_id": "hw",
            "stamp": 1.0,
            "level": 0,
            "msg": "OK",
            "values": [{"key": "x", "value": 1}],
        },
    )

    assert aggr.poll_once(timeout_ms=200)
    assert set(aggr.state_cache) == {"zmq/node", "ros/node"}
    assert sink.messages[-1].node == "ros/node"

    pub.close(linger=0)
    aggr.close()


def test_latest_source_message_wins_for_name_collision() -> None:
    endpoint = _free_tcp_endpoint()
    source_a = FakeSource([[_message("shared", level=0, msg="old")]])
    source_b = FakeSource([[_message("shared", level=2, msg="new")]])
    aggr = DiagnosticsAggregator(_config(endpoint), [], sources=[source_a, source_b], time_fn=FakeClock())

    assert aggr.poll_once(timeout_ms=0)
    assert aggr.state_cache["shared"].level == 2
    assert aggr.state_cache["shared"].msg == "new"
    aggr.close()


def test_stale_then_recovered() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    sink = FakeSink()
    clock = FakeClock()
    aggr = DiagnosticsAggregator(_config(endpoint), [sink], context=ctx, time_fn=clock)

    message = {
        "v": 1,
        "node": "controller/b",
        "hw_id": "arm",
        "stamp": 1.0,
        "level": 0,
        "msg": "OK",
        "values": [{"key": "x", "value": 1}],
    }

    assert aggr.process_raw(message)
    clock.advance(2.0)
    aggr.poll_once(timeout_ms=0)
    assert aggr.state_cache["controller/b"].level == 3
    assert aggr.state_cache["controller/b"].msg == "STALE"

    message["stamp"] = 3.0
    message["msg"] = "Recovered"
    assert aggr.process_raw(message)
    assert aggr.state_cache["controller/b"].level == 0

    aggr.close()


def test_ros_origin_message_uses_receive_time_for_stale_detection() -> None:
    endpoint = _free_tcp_endpoint()
    clock = FakeClock()
    source = FakeSource([[_message("ros/stale", msg="from ros")], []])
    aggr = DiagnosticsAggregator(_config(endpoint), [], sources=[source], time_fn=clock)

    assert aggr.poll_once(timeout_ms=0)
    clock.advance(2.0)
    aggr.poll_once(timeout_ms=0)

    assert aggr.state_cache["ros/stale"].level == 3
    assert aggr.state_cache["ros/stale"].msg == "STALE"
    aggr.close()


def test_schema_validation_rejects_invalid_message() -> None:
    invalid = {
        "v": 1,
        "node": "n1",
        "hw_id": "hw",
        "stamp": 0.0,
        "level": 0,
        "msg": "OK",
        "values": [{"key": "missing_value"}],
    }
    with pytest.raises(ValueError):
        DiagnosticsAggregator.validate_and_normalize_message(invalid)


def test_schema_validation_accepts_two_item_values_entries() -> None:
    valid = {
        "v": 1,
        "node": "n1",
        "hw_id": "hw",
        "stamp": 0.0,
        "level": 0,
        "msg": "OK",
        "values": [["temperature", 42.0]],
    }
    normalized = DiagnosticsAggregator.validate_and_normalize_message(valid)
    assert normalized.values[0].key == "temperature"
    assert normalized.values[0].value == 42.0


def test_multi_node_different_rates() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    clock = FakeClock()
    aggr = DiagnosticsAggregator(_config(endpoint), [FakeSink()], context=ctx, time_fn=clock)

    assert aggr.process_raw(
        {
            "v": 1,
            "node": "slow_node",
            "hw_id": "h1",
            "stamp": 1.0,
            "level": 0,
            "msg": "OK",
            "values": [{"key": "a", "value": 1}],
        }
    )
    for i in range(3):
        assert aggr.process_raw(
            {
                "v": 1,
                "node": "fast_node",
                "hw_id": "h2",
                "stamp": 2.0 + i,
                "level": 0,
                "msg": "OK",
                "values": [{"key": "b", "value": i}],
            }
        )

    clock.advance(1.5)
    aggr.poll_once(timeout_ms=0)
    assert aggr.state_cache["slow_node"].level == 3
    assert aggr.state_cache["fast_node"].level == 3
    aggr.close()


def test_graceful_shutdown_closes_sources() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    zmq_source = ZmqDiagnosticsSource(endpoint, context=ctx)
    fake_source = FakeSource([])
    aggr = DiagnosticsAggregator(
        _config(endpoint), [], sources=[zmq_source, fake_source], time_fn=FakeClock()
    )
    aggr.close()
    assert zmq_source._socket.closed
    assert fake_source.closed
