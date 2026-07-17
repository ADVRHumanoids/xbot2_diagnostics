import zmq

from pyxbot2_diagnostics.aggregator.aggregator import ZmqDiagnosticsSource
from pyxbot2_diagnostics.host_monitor.collectors import MetricSample
from pyxbot2_diagnostics.host_monitor.config import HostMonitorConfig
from pyxbot2_diagnostics.host_monitor.health import AlertObservation
from pyxbot2_diagnostics.host_monitor.monitor import HostMonitor


class FakeCollector:
    name = "cpu"

    def __init__(self, value: float = 50.0) -> None:
        self.value = value
        self.fail = False

    def sample(self, now: float):
        del now
        if self.fail:
            raise PermissionError("denied")
        return [MetricSample(
            "cpu",
            {"usage.percent": self.value},
            [AlertObservation("cpu.usage", "CPU usage", self.value, 90.0, 98.0, unit="%")],
            f"CPU {self.value:.1f}%",
        )]


def test_monitor_message_is_accepted_by_real_aggregator_source() -> None:
    context = zmq.Context()
    endpoint = "inproc://host-monitor-integration"
    source = ZmqDiagnosticsSource(endpoint, context=context)
    config = HostMonitorConfig(zmq_endpoint=endpoint, hostname="field-pc", hw_id="robot-01")
    monitor = HostMonitor(config, collectors=[FakeCollector()], context=context, monotonic_fn=lambda: 10.0)

    monitor.sample_once()
    messages = source.poll(timeout_ms=100)
    assert len(messages) == 1
    message = messages[0]
    assert message.node == "host/field-pc/cpu"
    assert message.hw_id == "robot-01"
    assert message.level == 0
    assert message.msg == "CPU 50.0%"
    assert {item.key: item.value for item in message.values}["collector.available"] == 1.0

    monitor.close()
    source.close()
    context.term()


def test_monitor_threshold_and_collector_failure_are_published() -> None:
    context = zmq.Context()
    endpoint = "inproc://host-monitor-state"
    source = ZmqDiagnosticsSource(endpoint, context=context)
    collector = FakeCollector(value=95.0)
    config = HostMonitorConfig(zmq_endpoint=endpoint, hostname="pc")
    monitor = HostMonitor(config, collectors=[collector], context=context, monotonic_fn=lambda: 10.0)

    for expected_level in (0, 0, 1):
        monitor.sample_once()
        message = source.poll(timeout_ms=100)[0]
        assert message.level == expected_level
        if expected_level == 0:
            assert message.msg == "CPU 95.0%"
        else:
            assert message.msg.startswith("WARN: CPU usage")

    collector.fail = True
    monitor.sample_once()
    failed = source.poll(timeout_ms=100)[0]
    assert failed.level == 1
    assert "PermissionError" in failed.msg
    assert {item.key: item.value for item in failed.values} == {"collector.available": 0.0}

    monitor.close()
    source.close()
    context.term()


def test_disconnected_monitor_drops_without_raising() -> None:
    config = HostMonitorConfig(zmq_endpoint="tcp://127.0.0.1:59999", hostname="offline")
    monitor = HostMonitor(config, collectors=[FakeCollector()])
    monitor.sample_once()
    monitor.sample_once()
    monitor.close()


def test_cli_once_applies_endpoint_override(monkeypatch) -> None:
    from pyxbot2_diagnostics.host_monitor import monitor as monitor_module

    events = []

    class FakeMonitor:
        def __init__(self, config):
            events.append(config)

        def sample_once(self):
            events.append("sample")

        def close(self):
            events.append("close")

    monkeypatch.setattr(
        monitor_module,
        "load_host_monitor_config",
        lambda path: HostMonitorConfig(zmq_endpoint="tcp://old:9268"),
    )
    monkeypatch.setattr(monitor_module, "HostMonitor", FakeMonitor)
    assert monitor_module.main(["--once", "--endpoint", "tcp://new:9268"]) == 0
    assert events[0].zmq_endpoint == "tcp://new:9268"
    assert events[1:] == ["sample", "close"]
