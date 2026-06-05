import json
import socket
from dataclasses import dataclass, field

import pytest
import zmq

from aggregator.aggregator import DiagnosticsAggregator
from aggregator.config import (
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


def test_graceful_shutdown_cleans_zmq() -> None:
    endpoint = _free_tcp_endpoint()
    ctx = zmq.Context()
    aggr = DiagnosticsAggregator(_config(endpoint), [], context=ctx, time_fn=FakeClock())
    aggr.close()
    assert aggr._socket.closed
