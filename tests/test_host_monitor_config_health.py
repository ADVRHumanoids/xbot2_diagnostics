from pathlib import Path

import pytest

from pyxbot2_diagnostics.host_monitor.config import (
    CONFIG_ENV_VAR,
    HostMonitorConfig,
    load_host_monitor_config,
)
from pyxbot2_diagnostics.host_monitor.health import AlertObservation, HealthEvaluator


def _high(value: float, key: str = "cpu") -> AlertObservation:
    return AlertObservation(key, "CPU usage", value, 90.0, 98.0, unit="%")


def test_host_monitor_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    config = load_host_monitor_config()
    assert config.zmq_endpoint == ""
    assert config.sample_interval_sec == 1.0
    assert config.hw_id == config.hostname
    assert config.aggregate_cpu_temperatures_only is True
    assert config.collectors.xenomai is True
    assert config.xenomai_stat_path == "/proc/xenomai/sched/stat"
    assert config.thresholds.consecutive_samples == 3
    assert "lo" in config.excluded_interfaces


def test_host_monitor_config_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "host.yaml"
    path.write_text(
        """
host_monitor:
  zmq_endpoint: "tcp://${AGGREGATOR_HOST}:9268"
  hostname: "field-pc"
  sample_interval_sec: 2
  aggregate_cpu_temperatures_only: false
  required_interfaces: [eth0]
  collectors:
    gpu: false
  thresholds:
    consecutive_samples: 2
    cpu_warn_percent: 80
    cpu_error_percent: 95
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGGREGATOR_HOST", "10.0.0.5")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(path))
    config = load_host_monitor_config()
    assert config.zmq_endpoint == "tcp://10.0.0.5:9268"
    assert config.hostname == "field-pc"
    assert config.hw_id == "field-pc"
    assert config.required_interfaces == ("eth0",)
    assert config.aggregate_cpu_temperatures_only is False
    assert config.collectors.gpu is False
    assert config.thresholds.cpu_warn_percent == 80.0


@pytest.mark.parametrize(
    "text",
    [
        "host_monitor:\n  sample_interval_sec: 0\n",
        "host_monitor:\n  required_interfaces: eth0\n",
        "host_monitor:\n  thresholds:\n    cpu_warn_percent: 99\n    cpu_error_percent: 95\n",
    ],
)
def test_invalid_host_monitor_config_raises(tmp_path: Path, text: str) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_host_monitor_config(str(path))


def test_health_threshold_debounce_escalation_and_hysteresis() -> None:
    evaluator = HealthEvaluator(consecutive_samples=3, recovery_margin=5.0)

    assert evaluator.evaluate([_high(92.0)]) == (0, "OK")
    assert evaluator.evaluate([_high(92.0)]) == (0, "OK")
    level, message = evaluator.evaluate([_high(92.0)])
    assert level == 1
    assert message.startswith("WARN: CPU usage")

    # Inside the five-point recovery margin, WARN remains active.
    assert evaluator.evaluate([_high(87.0)])[0] == 1

    # A direct transition to ERROR is also debounced.
    assert evaluator.evaluate([_high(99.0)])[0] == 1
    assert evaluator.evaluate([_high(99.0)])[0] == 1
    assert evaluator.evaluate([_high(99.0)])[0] == 2

    # ERROR first recovers to WARN, then WARN recovers to OK.
    for _ in range(3):
        result = evaluator.evaluate([_high(92.0)])
    assert result[0] == 1
    for _ in range(3):
        result = evaluator.evaluate([_high(84.0)])
    assert result == (0, "OK")


def test_low_threshold_and_highest_level() -> None:
    evaluator = HealthEvaluator(consecutive_samples=1, recovery_margin=5.0)
    battery = AlertObservation(
        "battery", "Battery charge", 9.0, 20.0, 10.0, direction="low", unit="%"
    )
    cpu = _high(91.0)
    level, message = evaluator.evaluate([battery, cpu])
    assert level == 2
    assert "Battery charge" in message
    assert "CPU usage" not in message
