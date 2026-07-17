from __future__ import annotations

from dataclasses import dataclass, field

from pyxbot2_diagnostics.aggregator.sources.ros_diagnostics_source import RosDiagnosticsSource


@dataclass
class Stamp:
    sec: int = 0
    nanosec: int = 0


@dataclass
class Header:
    stamp: Stamp | None = None


@dataclass
class KeyValue:
    key: str
    value: str


@dataclass
class Status:
    name: str
    hardware_id: str
    level: object
    message: str
    values: list[KeyValue] = field(default_factory=list)


@dataclass
class DiagnosticArray:
    header: Header = field(default_factory=Header)
    status: list[Status] = field(default_factory=list)


class FakeNode:
    def __init__(self) -> None:
        self.topic = ""
        self.callback = None
        self.destroyed_subscription = False
        self.destroyed_node = False

    def create_subscription(self, msg_type, topic, callback, qos):
        del msg_type, qos
        self.topic = topic
        self.callback = callback
        return object()

    def destroy_subscription(self, subscription) -> None:
        del subscription
        self.destroyed_subscription = True

    def destroy_node(self) -> None:
        self.destroyed_node = True


class FakeClock:
    def __init__(self, now: float = 42.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_ros_diagnostic_array_conversion() -> None:
    node = FakeNode()
    spin_calls = []
    source = RosDiagnosticsSource(
        node=node,
        diagnostic_array_type=DiagnosticArray,
        input_topic="/diagnostics",
        spin_once=lambda node, timeout_sec: spin_calls.append((node, timeout_sec)),
        time_fn=FakeClock(),
    )

    assert node.topic == "/diagnostics"
    assert node.callback is not None

    node.callback(
        DiagnosticArray(
            header=Header(Stamp(sec=10, nanosec=500_000_000)),
            status=[
                Status(
                    name="/xbot/thread/rt_main/load",
                    hardware_id="hw",
                    level=bytes([1]),
                    message="WARN",
                    values=[KeyValue("mean", "1.0")],
                )
            ],
        )
    )

    messages = source.poll(timeout_ms=25)
    assert spin_calls == [(node, 0.025)]
    assert len(messages) == 1
    assert messages[0].node == "/xbot/thread/rt_main/load"
    assert messages[0].hw_id == "hw"
    assert messages[0].level == 1
    assert messages[0].msg == "WARN"
    assert messages[0].stamp == 10.5
    assert messages[0].values[0].key == "mean"
    assert messages[0].values[0].value == "1.0"


def test_ros_diagnostic_array_falls_back_to_receive_time_without_stamp() -> None:
    node = FakeNode()
    source = RosDiagnosticsSource(
        node=node,
        diagnostic_array_type=DiagnosticArray,
        input_topic="/diagnostics",
        spin_once=lambda node, timeout_sec: None,
        time_fn=FakeClock(123.0),
    )

    node.callback(
        DiagnosticArray(
            header=Header(None),
            status=[Status(name="n", hardware_id="", level="2", message="ERROR")],
        )
    )

    messages = source.poll(timeout_ms=0)
    assert messages[0].stamp == 123.0
    assert messages[0].level == 2


def test_ros_source_close_destroys_subscription_node_and_context() -> None:
    node = FakeNode()
    shutdown_calls = []
    source = RosDiagnosticsSource(
        node=node,
        diagnostic_array_type=DiagnosticArray,
        input_topic="/diagnostics",
        spin_once=lambda node, timeout_sec: None,
        time_fn=FakeClock(),
        shutdown=lambda: shutdown_calls.append(True),
    )

    source.close()

    assert node.destroyed_subscription is True
    assert node.destroyed_node is True
    assert shutdown_calls == [True]
