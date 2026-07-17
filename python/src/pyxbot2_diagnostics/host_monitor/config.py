"""Configuration for the standalone host monitor."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV_VAR = "XBOT2_HOST_MONITOR_CONFIG"


@dataclass
class ThresholdConfig:
    consecutive_samples: int = 3
    recovery_margin: float = 5.0
    cpu_warn_percent: float = 90.0
    cpu_error_percent: float = 98.0
    ram_warn_percent: float = 85.0
    ram_error_percent: float = 95.0
    swap_warn_percent: float = 80.0
    swap_error_percent: float = 95.0
    filesystem_warn_percent: float = 85.0
    filesystem_error_percent: float = 95.0
    temperature_warn_c: float = 80.0
    temperature_error_c: float = 90.0
    battery_warn_percent: float = 20.0
    battery_error_percent: float = 10.0


@dataclass
class CollectorConfig:
    system: bool = True
    cpu: bool = True
    memory: bool = True
    temperature: bool = True
    filesystem: bool = True
    disk_io: bool = True
    network: bool = True
    battery: bool = True
    gpu: bool = True
    xenomai: bool = True


@dataclass
class HostMonitorConfig:
    zmq_endpoint: str = ""
    sample_interval_sec: float = 1.0
    hostname: str = field(default_factory=socket.gethostname)
    hw_id: str = ""
    node_prefix: str = "host"
    include_per_cpu: bool = True
    aggregate_cpu_temperatures_only: bool = True
    excluded_interfaces: tuple[str, ...] = (
        "lo",
        "docker*",
        "veth*",
        "br-*",
        "virbr*",
    )
    required_interfaces: tuple[str, ...] = ()
    excluded_filesystem_types: tuple[str, ...] = (
        "autofs",
        "binfmt_misc",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "overlay",
        "proc",
        "pstore",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    )
    gpu_command: str = "nvidia-smi"
    gpu_timeout_sec: float = 1.0
    xenomai_stat_path: str = "/proc/xenomai/sched/stat"
    collectors: CollectorConfig = field(default_factory=CollectorConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)

    def __post_init__(self) -> None:
        self.hostname = self.hostname or socket.gethostname()
        self.hw_id = self.hw_id or self.hostname
        self.node_prefix = self.node_prefix.strip("/") or "host"


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"host_monitor.{name} must be a mapping")
    return value


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_tuple(value: Any, default: tuple[str, ...], name: str) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"host_monitor.{name} must be a list of strings")
    return tuple(value)


def load_host_monitor_config(path: str | None = None) -> HostMonitorConfig:
    """Load host monitor YAML; an absent path returns validated defaults."""
    config_path = path or os.environ.get(CONFIG_ENV_VAR)
    raw: dict[str, Any] = {}
    if config_path:
        text = os.path.expandvars(Path(config_path).read_text(encoding="utf-8"))
        loaded = yaml.safe_load(text) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Top-level configuration must be a mapping")
        raw = _mapping(loaded.get("host_monitor"), "")

    defaults = HostMonitorConfig()
    collectors_raw = _mapping(raw.get("collectors"), "collectors")
    thresholds_raw = _mapping(raw.get("thresholds"), "thresholds")

    collectors = CollectorConfig(**{
        name: _as_bool(collectors_raw.get(name), getattr(defaults.collectors, name))
        for name in CollectorConfig.__dataclass_fields__
    })
    threshold_values: dict[str, Any] = {}
    for name in ThresholdConfig.__dataclass_fields__:
        default = getattr(defaults.thresholds, name)
        value = thresholds_raw.get(name, default)
        threshold_values[name] = int(value) if name == "consecutive_samples" else float(value)

    config = HostMonitorConfig(
        zmq_endpoint=str(raw.get("zmq_endpoint", "")),
        sample_interval_sec=float(raw.get("sample_interval_sec", defaults.sample_interval_sec)),
        hostname=str(raw.get("hostname", defaults.hostname)),
        hw_id=str(raw.get("hw_id", "")),
        node_prefix=str(raw.get("node_prefix", defaults.node_prefix)),
        include_per_cpu=_as_bool(raw.get("include_per_cpu"), defaults.include_per_cpu),
        aggregate_cpu_temperatures_only=_as_bool(
            raw.get("aggregate_cpu_temperatures_only"),
            defaults.aggregate_cpu_temperatures_only,
        ),
        excluded_interfaces=_as_tuple(
            raw.get("excluded_interfaces"), defaults.excluded_interfaces, "excluded_interfaces"
        ),
        required_interfaces=_as_tuple(
            raw.get("required_interfaces"), defaults.required_interfaces, "required_interfaces"
        ),
        excluded_filesystem_types=_as_tuple(
            raw.get("excluded_filesystem_types"),
            defaults.excluded_filesystem_types,
            "excluded_filesystem_types",
        ),
        gpu_command=str(raw.get("gpu_command", defaults.gpu_command)),
        gpu_timeout_sec=float(raw.get("gpu_timeout_sec", defaults.gpu_timeout_sec)),
        xenomai_stat_path=str(raw.get("xenomai_stat_path", defaults.xenomai_stat_path)),
        collectors=collectors,
        thresholds=ThresholdConfig(**threshold_values),
    )
    _validate(config)
    return config


def _validate(config: HostMonitorConfig) -> None:
    if config.sample_interval_sec <= 0:
        raise ValueError("host_monitor.sample_interval_sec must be > 0")
    if config.gpu_timeout_sec <= 0:
        raise ValueError("host_monitor.gpu_timeout_sec must be > 0")
    if not config.xenomai_stat_path:
        raise ValueError("host_monitor.xenomai_stat_path must be non-empty")
    if config.thresholds.consecutive_samples <= 0:
        raise ValueError("host_monitor.thresholds.consecutive_samples must be > 0")
    if config.thresholds.recovery_margin < 0:
        raise ValueError("host_monitor.thresholds.recovery_margin must be >= 0")
    pairs = (
        ("cpu", config.thresholds.cpu_warn_percent, config.thresholds.cpu_error_percent),
        ("ram", config.thresholds.ram_warn_percent, config.thresholds.ram_error_percent),
        ("swap", config.thresholds.swap_warn_percent, config.thresholds.swap_error_percent),
        ("filesystem", config.thresholds.filesystem_warn_percent, config.thresholds.filesystem_error_percent),
        ("temperature", config.thresholds.temperature_warn_c, config.thresholds.temperature_error_c),
    )
    for name, warn, error in pairs:
        if warn >= error:
            raise ValueError(f"host_monitor.thresholds.{name} WARN must be below ERROR")
    if config.thresholds.battery_error_percent >= config.thresholds.battery_warn_percent:
        raise ValueError("battery ERROR threshold must be below WARN")
