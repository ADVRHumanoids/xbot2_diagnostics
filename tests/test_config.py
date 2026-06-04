import os

import pytest

from aggregator.config import CONFIG_ENV_VAR, load_config


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    cfg = load_config()
    assert cfg.aggregator.zmq_endpoint == "tcp://localhost:5555"
    assert cfg.aggregator.stale_timeout_sec == 5.0
    assert cfg.sinks.influxdb.enabled is False


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
    assert cfg.sinks.stdout.interval_sec == 2.0


def test_invalid_config_raises(tmp_path) -> None:
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("aggregator:\n  stale_timeout_sec: -1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(cfg_path))
