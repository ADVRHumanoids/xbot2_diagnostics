"""Linux host metric collectors."""

from __future__ import annotations

import fnmatch
import math
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import psutil

from .config import HostMonitorConfig
from .health import AlertObservation


@dataclass
class MetricSample:
    path: str
    values: dict[str, float]
    alerts: list[AlertObservation] = field(default_factory=list)
    summary: str = ""


@dataclass(frozen=True)
class XenomaiSchedEntry:
    cpu: int
    pid: int
    msw: int
    load_percent: float
    name: str


def sanitize_name(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("_.-")
    return cleaned or fallback


def finite_values(values: dict[str, Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            output[key] = float(value)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            output[key] = float(value)
    return output


def _format_bytes(value: float) -> str:
    size = max(0.0, float(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _format_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds)) // 60
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def parse_xenomai_sched_stat(text: str) -> list[XenomaiSchedEntry]:
    """Parse /proc/xenomai/sched/stat, excluding Xenomai IRQ handler rows."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []

    headers = lines[0].upper().split()
    try:
        cpu_index = headers.index("CPU")
        pid_index = headers.index("PID")
        msw_index = headers.index("MSW")
        name_index = headers.index("NAME")
    except ValueError as exc:
        raise ValueError("Xenomai stat header must contain CPU, PID, MSW, and NAME") from exc

    load_index = next(
        (headers.index(name) for name in ("%CPU", "CPU%", "LOAD") if name in headers),
        None,
    )
    if load_index is None:
        raise ValueError("Xenomai stat header must contain %CPU or LOAD")

    entries: list[XenomaiSchedEntry] = []
    for line in lines[1:]:
        fields = line.split(maxsplit=name_index)
        if len(fields) <= name_index:
            continue
        name = fields[name_index].strip()
        if name.upper().startswith(("[IRQ", "IRQ")):
            continue
        try:
            entries.append(XenomaiSchedEntry(
                cpu=int(fields[cpu_index]),
                pid=int(fields[pid_index]),
                msw=int(fields[msw_index]),
                load_percent=float(fields[load_index]),
                name=name,
            ))
        except (ValueError, IndexError):
            continue
    return entries


class SystemCollector:
    name = "system"

    def sample(self, now: float) -> list[MetricSample]:
        del now
        boot_time = float(psutil.boot_time())
        uptime = max(0.0, time.time() - boot_time)
        logical_cpus = psutil.cpu_count(logical=True) or 0
        physical_cpus = psutil.cpu_count(logical=False) or 0
        process_count = len(psutil.pids())
        return [MetricSample("system", finite_values({
            "uptime.seconds": uptime,
            "boot.timestamp": boot_time,
            "cpu.logical_count": logical_cpus,
            "cpu.physical_count": physical_cpus,
            "process.count": process_count,
        }), summary=(
            f"Uptime {_format_duration(uptime)}, {process_count} processes, "
            f"{logical_cpus} logical CPUs"
        ))]


class CpuCollector:
    name = "cpu"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config
        self._previous_stats: Any | None = None
        self._previous_time: float | None = None

    def sample(self, now: float) -> list[MetricSample]:
        total = float(psutil.cpu_percent(interval=None))
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        load1, load5, load15 = psutil.getloadavg()
        values: dict[str, Any] = {
            "usage.percent": total,
            "load.1m": load1,
            "load.5m": load5,
            "load.15m": load15,
        }
        if self._config.include_per_cpu:
            values.update({f"core.{index}.percent": value for index, value in enumerate(per_cpu)})

        frequency = psutil.cpu_freq()
        if frequency is not None:
            values.update({
                "frequency.current_mhz": frequency.current,
                "frequency.min_mhz": frequency.min,
                "frequency.max_mhz": frequency.max,
            })

        stats = psutil.cpu_stats()
        if self._previous_stats is not None and self._previous_time is not None:
            elapsed = now - self._previous_time
            if elapsed > 0:
                for key in ("ctx_switches", "interrupts", "soft_interrupts"):
                    current = getattr(stats, key, None)
                    previous = getattr(self._previous_stats, key, None)
                    if current is not None and previous is not None:
                        values[f"{key}.per_sec"] = max(0.0, current - previous) / elapsed
        self._previous_stats = stats
        self._previous_time = now

        threshold = self._config.thresholds
        alert = AlertObservation(
            "cpu.usage", "CPU usage", total,
            threshold.cpu_warn_percent, threshold.cpu_error_percent, unit="%",
        )
        summary = f"CPU {total:.1f}%, load {load1:.2f}"
        if frequency is not None and math.isfinite(float(frequency.current)):
            summary += f", {frequency.current / 1000.0:.2f} GHz"
        return [MetricSample("cpu", finite_values(values), [alert], summary)]


class MemoryCollector:
    name = "memory"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config

    def sample(self, now: float) -> list[MetricSample]:
        del now
        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        values = finite_values({
            "ram.total_gb": ram.total / (1024 ** 3),
            "ram.used_gb": ram.used / (1024 ** 3),
            "ram.percent": ram.percent,
            "swap.total_gb": swap.total / (1024 ** 3),
            "swap.used_gb": swap.used / (1024 ** 3),
            "swap.percent": swap.percent,
        })
        threshold = self._config.thresholds
        alerts = [AlertObservation(
            "memory.ram", "RAM usage", float(ram.percent),
            threshold.ram_warn_percent, threshold.ram_error_percent, unit="%",
        )]
        if swap.total > 0:
            alerts.append(AlertObservation(
                "memory.swap", "Swap usage", float(swap.percent),
                threshold.swap_warn_percent, threshold.swap_error_percent, unit="%",
            ))
        summary = f"RAM {ram.percent:.1f}% ({ram.used / (1024 ** 3):.1f}/{ram.total / (1024 ** 3):.1f} GiB)"
        summary += f", swap {swap.percent:.1f}%" if swap.total > 0 else ", swap disabled"
        return [MetricSample("memory", values, alerts, summary)]


class TemperatureCollector:
    name = "temperature"

    _CPU_CHIP_NAMES = (
        "coretemp",
        "k10temp",
        "zenpower",
        "cpu_thermal",
        "cpu-thermal",
        "x86_pkg_temp",
    )

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config

    @classmethod
    def _is_cpu_sensor(cls, chip: str, label: str) -> bool:
        chip_lower = chip.lower()
        label_lower = label.lower()
        return (
            any(chip_lower.startswith(name) for name in cls._CPU_CHIP_NAMES)
            or re.search(r"\bcpu\b", label_lower) is not None
        )

    def sample(self, now: float) -> list[MetricSample]:
        del now
        sensors = psutil.sensors_temperatures(fahrenheit=False)
        if not sensors:
            return []
        values: dict[str, Any] = {}
        alerts: list[AlertObservation] = []
        cpu_temperatures: list[float] = []
        reported_temperatures: list[float] = []
        defaults = self._config.thresholds
        for chip, entries in sensors.items():
            chip_name = sanitize_name(chip)
            for index, entry in enumerate(entries):
                is_cpu_sensor = self._is_cpu_sensor(chip, entry.label or "")
                if self._config.aggregate_cpu_temperatures_only and not is_cpu_sensor:
                    continue
                label = sanitize_name(entry.label or str(index))
                base = f"sensor.{chip_name}.{label}"
                current = float(entry.current)
                if math.isfinite(current):
                    reported_temperatures.append(current)
                if self._config.aggregate_cpu_temperatures_only:
                    if math.isfinite(current):
                        cpu_temperatures.append(current)
                else:
                    values[f"{base}.current_c"] = entry.current
                    if entry.high is not None:
                        values[f"{base}.high_c"] = entry.high
                    if entry.critical is not None:
                        values[f"{base}.critical_c"] = entry.critical
                warn = (
                    float(entry.high) if entry.high is not None and entry.high > 0
                    else float(entry.critical) - 10.0
                    if entry.critical is not None and entry.critical > 0
                    else defaults.temperature_warn_c
                )
                error = (
                    float(entry.critical) if entry.critical is not None and entry.critical > warn
                    else max(defaults.temperature_error_c, warn + 5.0)
                )
                alerts.append(AlertObservation(
                    f"temperature.{chip_name}.{label}",
                    f"{chip}/{entry.label or index}", float(entry.current), warn, error, unit="°C",
                ))
        if self._config.aggregate_cpu_temperatures_only:
            if not cpu_temperatures:
                return []
            minimum = min(cpu_temperatures)
            average = sum(cpu_temperatures) / len(cpu_temperatures)
            maximum = max(cpu_temperatures)
            values.update({
                "cpu_temperature.min_c": minimum,
                "cpu_temperature.avg_c": average,
                "cpu_temperature.max_c": maximum,
            })
            summary = f"CPU temp min/avg/max {minimum:.1f}/{average:.1f}/{maximum:.1f} °C"
        else:
            if not reported_temperatures:
                return []
            minimum = min(reported_temperatures)
            average = sum(reported_temperatures) / len(reported_temperatures)
            maximum = max(reported_temperatures)
            summary = (
                f"{len(reported_temperatures)} temperatures, min/avg/max "
                f"{minimum:.1f}/{average:.1f}/{maximum:.1f} °C"
            )
        return [MetricSample("temperature", finite_values(values), alerts, summary)]


class FilesystemCollector:
    name = "filesystem"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config

    def sample(self, now: float) -> list[MetricSample]:
        del now
        values: dict[str, Any] = {}
        alerts: list[AlertObservation] = []
        seen: set[str] = set()
        highest_mount = ""
        highest_percent = -1.0
        thresholds = self._config.thresholds
        for partition in psutil.disk_partitions(all=False):
            if partition.mountpoint in seen or partition.fstype in self._config.excluded_filesystem_types:
                continue
            seen.add(partition.mountpoint)
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError):
                continue
            mount = "root" if partition.mountpoint == "/" else sanitize_name(partition.mountpoint)
            base = f"mount.{mount}"
            values.update({
                f"{base}.total_gb": usage.total / (1024 ** 3),
                f"{base}.used_gb": usage.used / (1024 ** 3),
                f"{base}.percent": usage.percent,
            })
            if usage.percent > highest_percent:
                highest_mount = partition.mountpoint
                highest_percent = float(usage.percent)
            alerts.append(AlertObservation(
                f"filesystem.{mount}", f"Filesystem {partition.mountpoint}", float(usage.percent),
                thresholds.filesystem_warn_percent, thresholds.filesystem_error_percent, unit="%",
            ))
        if not values:
            return []
        summary = f"{len(alerts)} filesystems, highest {highest_mount} at {highest_percent:.1f}%"
        return [MetricSample("filesystem", finite_values(values), alerts, summary)]


class DiskIoCollector:
    name = "disk_io"

    def __init__(self) -> None:
        self._previous: Any | None = None
        self._previous_time: float | None = None

    def sample(self, now: float) -> list[MetricSample]:
        counters = psutil.disk_io_counters(perdisk=False, nowrap=True)
        if counters is None:
            return []
        values: dict[str, Any] = {
            "read.total_bytes": counters.read_bytes,
            "write.total_bytes": counters.write_bytes,
        }
        if self._previous is not None and self._previous_time is not None:
            elapsed = now - self._previous_time
            if elapsed > 0:
                for key, attr in (
                    ("read.bytes_per_sec", "read_bytes"),
                    ("write.bytes_per_sec", "write_bytes"),
                ):
                    values[key] = max(0.0, getattr(counters, attr) - getattr(self._previous, attr)) / elapsed
                if hasattr(counters, "busy_time") and hasattr(self._previous, "busy_time"):
                    delta_ms = max(0.0, counters.busy_time - self._previous.busy_time)
                    values["busy.percent"] = min(100.0, delta_ms / (elapsed * 10.0))
        self._previous = counters
        self._previous_time = now
        if "read.bytes_per_sec" in values:
            summary = (
                f"Disk I/O R {_format_bytes(values['read.bytes_per_sec'])}/s, "
                f"W {_format_bytes(values['write.bytes_per_sec'])}/s"
            )
            if "busy.percent" in values:
                summary += f", busy {values['busy.percent']:.1f}%"
        else:
            summary = (
                f"Disk I/O totals R {_format_bytes(counters.read_bytes)}, "
                f"W {_format_bytes(counters.write_bytes)}"
            )
        return [MetricSample("disk_io", finite_values(values), summary=summary)]


class NetworkCollector:
    name = "network"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config
        self._previous: dict[str, Any] = {}
        self._previous_time: float | None = None

    def _excluded(self, interface: str) -> bool:
        return any(fnmatch.fnmatch(interface, pattern) for pattern in self._config.excluded_interfaces)

    def sample(self, now: float) -> list[MetricSample]:
        counters = psutil.net_io_counters(pernic=True, nowrap=True)
        try:
            stats = psutil.net_if_stats()
        except (OSError, PermissionError):
            stats = {}
        samples: list[MetricSample] = []
        elapsed = now - self._previous_time if self._previous_time is not None else 0.0
        included = {name for name in counters if not self._excluded(name)}
        included.update(self._config.required_interfaces)
        for interface in sorted(included):
            current = counters.get(interface)
            stat = stats.get(interface)
            values: dict[str, Any] = {}
            if current is not None:
                values.update({
                    "rx.total_bytes": current.bytes_recv,
                    "tx.total_bytes": current.bytes_sent,
                    "rx.total_packets": current.packets_recv,
                    "tx.total_packets": current.packets_sent,
                    "rx.total_errors": current.errin,
                    "tx.total_errors": current.errout,
                    "rx.total_drops": current.dropin,
                    "tx.total_drops": current.dropout,
                })
                previous = self._previous.get(interface)
                if previous is not None and elapsed > 0:
                    for key, attr in (
                        ("rx.bytes_per_sec", "bytes_recv"),
                        ("tx.bytes_per_sec", "bytes_sent"),
                        ("rx.packets_per_sec", "packets_recv"),
                        ("tx.packets_per_sec", "packets_sent"),
                        ("rx.errors_per_sec", "errin"),
                        ("tx.errors_per_sec", "errout"),
                        ("rx.drops_per_sec", "dropin"),
                        ("tx.drops_per_sec", "dropout"),
                    ):
                        values[key] = max(0.0, getattr(current, attr) - getattr(previous, attr)) / elapsed
            if stat is not None:
                values.update({"link.up": stat.isup, "link.speed_mbps": stat.speed, "link.mtu": stat.mtu})

            alerts: list[AlertObservation] = []
            if interface in self._config.required_interfaces:
                is_up = 1.0 if stat is not None and stat.isup else 0.0
                values["link.up"] = is_up
                alerts.append(AlertObservation(
                    f"network.{interface}.required", f"Interface {interface} link", is_up,
                    warn=0.5, error=None, direction="low",
                ))
            if stat is None:
                link_summary = "link unknown"
            else:
                link_summary = "up" if stat.isup else "down"
            if "rx.bytes_per_sec" in values:
                summary = (
                    f"{interface} {link_summary}, RX {_format_bytes(values['rx.bytes_per_sec'])}/s, "
                    f"TX {_format_bytes(values['tx.bytes_per_sec'])}/s"
                )
            elif current is not None:
                summary = (
                    f"{interface} {link_summary}, totals RX {_format_bytes(current.bytes_recv)}, "
                    f"TX {_format_bytes(current.bytes_sent)}"
                )
            else:
                summary = f"{interface} {link_summary}, counters unavailable"
            samples.append(MetricSample(
                f"network.{sanitize_name(interface)}", finite_values(values), alerts, summary
            ))
        self._previous = dict(counters)
        self._previous_time = now
        return samples


class BatteryCollector:
    name = "battery"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._config = config

    def sample(self, now: float) -> list[MetricSample]:
        del now
        battery = psutil.sensors_battery()
        if battery is None:
            return []
        values = finite_values({
            "charge.percent": battery.percent,
            "seconds_left": battery.secsleft,
            "power_plugged": battery.power_plugged,
        })
        alerts: list[AlertObservation] = []
        if not battery.power_plugged:
            thresholds = self._config.thresholds
            alerts.append(AlertObservation(
                "battery.charge", "Battery charge", float(battery.percent),
                thresholds.battery_warn_percent, thresholds.battery_error_percent,
                direction="low", unit="%",
            ))
        if battery.power_plugged:
            summary = f"Battery {battery.percent:.1f}%, plugged in"
        else:
            summary = f"Battery {battery.percent:.1f}%, discharging"
            if battery.secsleft is not None and battery.secsleft > 0:
                summary += f", {_format_duration(battery.secsleft)} remaining"
        return [MetricSample("battery", values, alerts, summary)]


class NvidiaGpuCollector:
    name = "gpu"

    def __init__(
        self,
        config: HostMonitorConfig,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._config = config
        self._runner = runner
        self._unsupported = False
        self._has_succeeded = False

    def sample(self, now: float) -> list[MetricSample]:
        del now
        if self._unsupported:
            return []
        command = [
            self._config.gpu_command,
            "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = self._runner(
                command, capture_output=True, text=True,
                timeout=self._config.gpu_timeout_sec, check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if not self._has_succeeded:
                self._unsupported = True
                return []
            raise
        samples: list[MetricSample] = []
        thresholds = self._config.thresholds
        for line in result.stdout.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 5:
                continue
            try:
                index, utilization, used, total, temperature = (
                    int(fields[0]), float(fields[1]), float(fields[2]), float(fields[3]), float(fields[4])
                )
            except ValueError:
                continue
            samples.append(MetricSample(
                f"gpu.{index}",
                finite_values({
                    "utilization.percent": utilization,
                    "memory.used_mib": used,
                    "memory.total_mib": total,
                    "temperature.c": temperature,
                }),
                [AlertObservation(
                    f"gpu.{index}.temperature", f"GPU {index} temperature", temperature,
                    thresholds.temperature_warn_c, thresholds.temperature_error_c, unit="°C",
                )],
                (
                    f"GPU {index} {utilization:.1f}%, memory {used:.0f}/{total:.0f} MiB, "
                    f"{temperature:.1f} °C"
                ),
            ))
        if samples:
            self._has_succeeded = True
        return samples


class XenomaiProcCollector:
    name = "xenomai"

    def __init__(self, config: HostMonitorConfig) -> None:
        self._stat_path = Path(config.xenomai_stat_path)

    def sample(self, now: float) -> list[MetricSample]:
        del now
        try:
            text = self._stat_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []

        samples: list[MetricSample] = []
        for entry in parse_xenomai_sched_stat(text):
            entry_name = sanitize_name(entry.name)
            path = f"xenomai.{entry_name}.{entry.cpu}.{entry.pid}"
            samples.append(MetricSample(
                path,
                finite_values({
                    "msw.count": entry.msw,
                    "load.percent": entry.load_percent,
                }),
                summary=(
                    f"{entry.name}: load {entry.load_percent:.1f}%, MSW {entry.msw} "
                    f"(CPU {entry.cpu}, PID {entry.pid})"
                ),
            ))
        return samples


def build_collectors(config: HostMonitorConfig) -> list[Any]:
    enabled = config.collectors
    candidates = (
        (enabled.system, SystemCollector()),
        (enabled.cpu, CpuCollector(config)),
        (enabled.memory, MemoryCollector(config)),
        (enabled.temperature, TemperatureCollector(config)),
        (enabled.filesystem, FilesystemCollector(config)),
        (enabled.disk_io, DiskIoCollector()),
        (enabled.network, NetworkCollector(config)),
        (enabled.battery, BatteryCollector(config)),
        (enabled.gpu, NvidiaGpuCollector(config)),
        (enabled.xenomai, XenomaiProcCollector(config)),
    )
    return [collector for is_enabled, collector in candidates if is_enabled]
