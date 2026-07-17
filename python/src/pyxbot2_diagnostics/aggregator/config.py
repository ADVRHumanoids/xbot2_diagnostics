"""Configuration loading for diagnostics aggregator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CONFIG_ENV_VAR = "XBOT2_DIAGNOSTICS_CONFIG"


@dataclass
class AggregatorSection:
    zmq_endpoint: str = "tcp://localhost:9268"
    stale_timeout_sec: float = 5.0
    stale_check_interval_sec: float = 1.0


@dataclass
class InfluxDBSection:
    enabled: bool = False
    url: str = ""
    token: str = ""
    org: str = ""
    bucket: str = ""


@dataclass
class RosDiagnosticsSection:
    enabled: bool = False
    input_topic: str = "/diagnostics"
    aggregated_topic: str = "/diagnostics_agg"
    publish_aggregated: bool = True
    aggregation_root: str = "Robot"


@dataclass
class JsonFileSection:
    enabled: bool = False
    path: str = "/tmp/diagnostics.jsonl"
    max_file_size_mb: float = 100.0


@dataclass
class StdoutSection:
    enabled: bool = False
    interval_sec: float = 10.0


@dataclass
class SinksSection:
    influxdb: InfluxDBSection = field(default_factory=InfluxDBSection)
    ros_diagnostics: RosDiagnosticsSection = field(default_factory=RosDiagnosticsSection)
    json_file: JsonFileSection = field(default_factory=JsonFileSection)
    stdout: StdoutSection = field(default_factory=StdoutSection)


@dataclass
class AggregatorConfig:
    aggregator: AggregatorSection = field(default_factory=AggregatorSection)
    sinks: SinksSection = field(default_factory=SinksSection)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Section '{key}' must be a mapping")
    return value


def _expand_env(text: str) -> str:
    return os.path.expandvars(text)


def load_config(path: str | None = None) -> AggregatorConfig:
    """Load YAML configuration from *path* or environment variable."""
    config_path = path or os.environ.get(CONFIG_ENV_VAR)
    if not config_path:
        return AggregatorConfig()

    raw_text = Path(config_path).read_text(encoding="utf-8")
    expanded_text = _expand_env(raw_text)
    raw = yaml.safe_load(expanded_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("Top-level configuration must be a mapping")

    agg = _section(raw, "aggregator")
    sinks = _section(raw, "sinks")
    influxdb = _section(sinks, "influxdb")
    ros_diagnostics = _section(sinks, "ros_diagnostics")
    json_file = _section(sinks, "json_file")
    stdout = _section(sinks, "stdout")

    cfg = AggregatorConfig(
        aggregator=AggregatorSection(
            zmq_endpoint=str(agg.get("zmq_endpoint", "tcp://localhost:9268")),
            stale_timeout_sec=float(agg.get("stale_timeout_sec", 5.0)),
            stale_check_interval_sec=float(agg.get("stale_check_interval_sec", 1.0)),
        ),
        sinks=SinksSection(
            influxdb=InfluxDBSection(
                enabled=_as_bool(influxdb.get("enabled"), False),
                url=str(influxdb.get("url", "")),
                token=str(influxdb.get("token", "")),
                org=str(influxdb.get("org", "")),
                bucket=str(influxdb.get("bucket", "")),
            ),
            ros_diagnostics=RosDiagnosticsSection(
                enabled=_as_bool(ros_diagnostics.get("enabled"), False),
                input_topic=str(ros_diagnostics.get("input_topic", "/diagnostics")),
                aggregated_topic=str(ros_diagnostics.get("aggregated_topic", "/diagnostics_agg")),
                publish_aggregated=_as_bool(ros_diagnostics.get("publish_aggregated"), True),
                aggregation_root=str(ros_diagnostics.get("aggregation_root", "Robot")),
            ),
            json_file=JsonFileSection(
                enabled=_as_bool(json_file.get("enabled"), False),
                path=str(json_file.get("path", "/tmp/diagnostics.jsonl")),
                max_file_size_mb=float(json_file.get("max_file_size_mb", 100.0)),
            ),
            stdout=StdoutSection(
                enabled=_as_bool(stdout.get("enabled"), False),
                interval_sec=float(stdout.get("interval_sec", 10.0)),
            ),
        ),
    )

    if cfg.aggregator.stale_timeout_sec <= 0:
        raise ValueError("aggregator.stale_timeout_sec must be > 0")
    if cfg.aggregator.stale_check_interval_sec <= 0:
        raise ValueError("aggregator.stale_check_interval_sec must be > 0")
    if cfg.sinks.stdout.interval_sec <= 0:
        raise ValueError("sinks.stdout.interval_sec must be > 0")
    if cfg.sinks.json_file.max_file_size_mb <= 0:
        raise ValueError("sinks.json_file.max_file_size_mb must be > 0")
    if cfg.sinks.ros_diagnostics.enabled:
        if not cfg.sinks.ros_diagnostics.input_topic:
            raise ValueError("sinks.ros_diagnostics.input_topic must be non-empty")
        if cfg.sinks.ros_diagnostics.publish_aggregated and not cfg.sinks.ros_diagnostics.aggregated_topic:
            raise ValueError("sinks.ros_diagnostics.aggregated_topic must be non-empty")
        if not cfg.sinks.ros_diagnostics.aggregation_root.strip("/"):
            raise ValueError("sinks.ros_diagnostics.aggregation_root must be non-empty")

    return cfg
