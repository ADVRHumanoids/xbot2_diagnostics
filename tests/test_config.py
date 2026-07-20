import os

import pytest

from pyxbot2_diagnostics.aggregator.config import CONFIG_ENV_VAR, load_config


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    cfg = load_config()
    assert cfg.aggregator.zmq_endpoint == "tcp://localhost:9268"
    assert cfg.aggregator.stale_timeout_sec == 5.0
    assert cfg.sinks.influxdb.enabled is False
    assert cfg.sinks.ros_diagnostics.input_topic == "/diagnostics"
    assert cfg.sinks.ros_diagnostics.aggregated_topic == "/diagnostics_agg"
    assert cfg.sinks.ros_diagnostics.publish_aggregated is True
    assert cfg.sinks.ros_diagnostics.publish_rate_hz == 1.0
    assert cfg.sinks.ros_diagnostics.aggregation_root == "Robot"


def test_load_config_from_env_with_expansion(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    monkeypatch.setenv("INFLUXDB_TOKEN", "abc123")
    cfg_path.write_text(
        """
aggregator:
  zmq_endpoint: "tcp://127.0.0.1:6000"
sinks:
  influxdb:
    enabled: true
    token: "${INFLUXDB_TOKEN}"
  ros_diagnostics:
    enabled: true
    input_topic: "/diagnostics"
    aggregated_topic: "/robot_monitor/diagnostics_agg"
    publish_aggregated: true
    publish_rate_hz: 2.5
    aggregation_root: "Robot"
  stdout:
    enabled: true
    interval_sec: 2.0
""",
        encoding="utf-8",
    )
    monkeypatch.setenv(CONFIG_ENV_VAR, str(cfg_path))

    cfg = load_config()
    assert cfg.aggregator.zmq_endpoint == "tcp://127.0.0.1:6000"
    assert cfg.sinks.influxdb.enabled is True
    assert cfg.sinks.influxdb.token == "abc123"
    assert cfg.sinks.ros_diagnostics.enabled is True
    assert cfg.sinks.ros_diagnostics.input_topic == "/diagnostics"
    assert cfg.sinks.ros_diagnostics.aggregated_topic == "/robot_monitor/diagnostics_agg"
    assert cfg.sinks.ros_diagnostics.publish_aggregated is True
    assert cfg.sinks.ros_diagnostics.publish_rate_hz == 2.5
    assert cfg.sinks.ros_diagnostics.aggregation_root == "Robot"
    assert cfg.sinks.stdout.interval_sec == 2.0


def test_invalid_config_raises(tmp_path) -> None:
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("aggregator:\n  stale_timeout_sec: -1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(cfg_path))


def test_ros_diagnostics_requires_input_topic(tmp_path) -> None:
    cfg_path = tmp_path / "bad_ros.yaml"
    cfg_path.write_text(
        """
sinks:
  ros_diagnostics:
    enabled: true
    input_topic: ""
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(str(cfg_path))


def test_ros_diagnostics_requires_positive_publish_rate(tmp_path) -> None:
    cfg_path = tmp_path / "bad_ros_rate.yaml"
    cfg_path.write_text(
        """
sinks:
  ros_diagnostics:
    enabled: true
    publish_rate_hz: 0
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="publish_rate_hz"):
        load_config(str(cfg_path))
