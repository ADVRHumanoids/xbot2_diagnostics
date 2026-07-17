from pathlib import Path
import tomllib

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: str):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_diagnostics_compose_and_grafana_datasource_parse() -> None:
    compose = _load_yaml("docker/docker-compose.diagnostics.yml")
    assert {"influxdb", "grafana"}.issubset(compose["services"])
    assert compose["services"]["grafana"]["volumes"]

    datasource = _load_yaml("docker/grafana/provisioning/datasources/influxdb.yaml")
    assert datasource["datasources"][0]["type"] == "influxdb"
    assert datasource["datasources"][0]["url"] == "http://influxdb:8086"


def test_diagnostic_remote_logging_config_targets_local_influx() -> None:
    config = _load_yaml("config/diagnostic_remote_logging.yaml")
    params = config["/influxdb_connector"]["ros__parameters"]
    assert params["connection"]["url"] == "http://localhost:8086/api/v2/write"
    assert params["connection"]["bucket"] == "diagnostics"
    assert params["send"]["diagnostics"] is True
    assert params["send"]["top_level_state"] is False


def test_aggregator_config_publishes_aggregated_diagnostics() -> None:
    config = _load_yaml("config/aggregator.yaml")
    ros = config["sinks"]["ros_diagnostics"]
    assert ros["input_topic"] == "/diagnostics"
    assert ros["aggregated_topic"] == "/diagnostics_agg"
    assert ros["publish_aggregated"] is True


def test_host_monitor_assets() -> None:
    config = _load_yaml("config/host_monitor.yaml")["host_monitor"]
    assert config["sample_interval_sec"] == 1.0
    assert config["thresholds"]["consecutive_samples"] == 3
    assert config["collectors"]["network"] is True

    service = (ROOT / "services/xbot2-host-monitor.service.in").read_text(encoding="utf-8")
    assert "WantedBy=multi-user.target" in service
    assert "Restart=on-failure" in service
    assert "NoNewPrivileges=yes" in service

    pyproject = tomllib.loads((ROOT / "python/pyproject.toml").read_text(encoding="utf-8"))
    assert "psutil>=5.9" in pyproject["project"]["dependencies"]
    assert pyproject["project"]["scripts"]["xbot2-host-monitor"].endswith(":main")


@pytest.mark.parametrize(
    "launch_file",
    [
        "launch/xbot2_diagnostics_aggregator.launch.py",
        "launch/diagnostic_remote_logging.launch.py",
    ],
)
def test_launch_files_compile(launch_file: str) -> None:
    compile((ROOT / launch_file).read_text(encoding="utf-8"), launch_file, "exec")
