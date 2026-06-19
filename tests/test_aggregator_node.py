import sys
from types import ModuleType, SimpleNamespace

from pyxbot2_diagnostics.aggregator import aggregator_node
from pyxbot2_diagnostics.aggregator.config import (
    AggregatorConfig,
    RosDiagnosticsSection,
    SinksSection,
)


class FakeNode:
    instances = []

    def __init__(self, name: str) -> None:
        self.name = name
        self.subscriptions = []
        self.publishers = []
        self.destroyed = False
        FakeNode.instances.append(self)

    def create_subscription(self, msg_type, topic, callback, qos):
        self.subscriptions.append((msg_type, topic, callback, qos))
        return object()

    def create_publisher(self, msg_type, topic, qos):
        publisher = SimpleNamespace(topic=topic, messages=[])
        publisher.publish = publisher.messages.append
        self.publishers.append((msg_type, topic, qos, publisher))
        return publisher

    def get_clock(self):
        stamp = SimpleNamespace(sec=0, nanosec=0)
        return SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: stamp))

    def destroy_subscription(self, subscription) -> None:
        del subscription

    def destroy_node(self) -> None:
        self.destroyed = True


def test_build_ros_io_subscribes_to_diagnostics_and_only_publishes_aggregated(monkeypatch):
    FakeNode.instances = []
    init_calls = []
    shutdown_calls = []

    rclpy = ModuleType("rclpy")
    rclpy.init = lambda args=None: init_calls.append(args)
    rclpy.spin_once = lambda node, timeout_sec=0.0: None
    rclpy.shutdown = lambda: shutdown_calls.append(True)

    rclpy_node = ModuleType("rclpy.node")
    rclpy_node.Node = FakeNode

    diagnostic_msgs = ModuleType("diagnostic_msgs")
    diagnostic_msgs_msg = ModuleType("diagnostic_msgs.msg")
    diagnostic_msgs_msg.DiagnosticArray = object

    monkeypatch.setitem(sys.modules, "rclpy", rclpy)
    monkeypatch.setitem(sys.modules, "rclpy.node", rclpy_node)
    monkeypatch.setitem(sys.modules, "diagnostic_msgs", diagnostic_msgs)
    monkeypatch.setitem(sys.modules, "diagnostic_msgs.msg", diagnostic_msgs_msg)

    config = AggregatorConfig(
        sinks=SinksSection(
            ros_diagnostics=RosDiagnosticsSection(
                enabled=True,
                input_topic="/diagnostics",
                aggregated_topic="/diagnostics_agg",
            )
        )
    )

    source, sink = aggregator_node._build_ros_io(config)

    assert source is not None
    assert sink is not None
    assert init_calls == [None]
    node = FakeNode.instances[0]
    assert [topic for _, topic, _, _ in node.subscriptions] == ["/diagnostics"]
    assert [topic for _, topic, _, _ in node.publishers] == ["/diagnostics_agg"]

    source.close()
    assert node.destroyed is True
    assert shutdown_calls == [True]
