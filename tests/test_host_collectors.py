from types import SimpleNamespace

import pytest

from pyxbot2_diagnostics.host_monitor import collectors
from pyxbot2_diagnostics.host_monitor.collectors import (
    BatteryCollector,
    CpuCollector,
    DiskIoCollector,
    FilesystemCollector,
    MemoryCollector,
    NetworkCollector,
    NvidiaGpuCollector,
    SystemCollector,
    TemperatureCollector,
    XenomaiProcCollector,
    finite_values,
    parse_xenomai_sched_stat,
    sanitize_name,
)
from pyxbot2_diagnostics.host_monitor.config import HostMonitorConfig


def test_sanitize_and_filter_non_finite_values() -> None:
    assert sanitize_name("/boot/efi") == "boot_efi"
    assert finite_values({"ok": 1, "bool": True, "nan": float("nan"), "text": "x"}) == {
        "ok": 1.0,
        "bool": 1.0,
    }


def test_system_collector_uses_wall_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collectors.time, "time", lambda: 150.0)
    monkeypatch.setattr(collectors.psutil, "boot_time", lambda: 100.0)
    monkeypatch.setattr(collectors.psutil, "cpu_count", lambda logical=True: 8 if logical else 4)
    monkeypatch.setattr(collectors.psutil, "pids", lambda: [1, 2, 3])
    sample = SystemCollector().sample(9999.0)[0]
    assert sample.values["uptime.seconds"] == 50.0
    assert sample.values["cpu.physical_count"] == 4.0
    assert sample.values["process.count"] == 3.0
    assert sample.summary == "Uptime 0m, 3 processes, 8 logical CPUs"


def test_cpu_collector_rates_and_per_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    config = HostMonitorConfig()
    stats = iter([
        SimpleNamespace(ctx_switches=100, interrupts=50, soft_interrupts=10),
        SimpleNamespace(ctx_switches=120, interrupts=54, soft_interrupts=12),
    ])
    monkeypatch.setattr(
        collectors.psutil,
        "cpu_percent",
        lambda interval=None, percpu=False: [20.0, 40.0] if percpu else 30.0,
    )
    monkeypatch.setattr(collectors.psutil, "getloadavg", lambda: (1.0, 2.0, 3.0))
    monkeypatch.setattr(
        collectors.psutil, "cpu_freq", lambda: SimpleNamespace(current=2000, min=800, max=3000)
    )
    monkeypatch.setattr(collectors.psutil, "cpu_stats", lambda: next(stats))
    collector = CpuCollector(config)
    first = collector.sample(10.0)[0]
    second = collector.sample(12.0)[0]
    assert "ctx_switches.per_sec" not in first.values
    assert second.values["ctx_switches.per_sec"] == 10.0
    assert second.values["interrupts.per_sec"] == 2.0
    assert second.values["core.1.percent"] == 40.0
    assert second.alerts[0].value == 30.0
    assert second.summary == "CPU 30.0%, load 1.00, 2.00 GHz"


def test_memory_and_battery_collectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collectors.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=1000, available=200, used=800, percent=80.0),
    )
    monkeypatch.setattr(
        collectors.psutil,
        "swap_memory",
        lambda: SimpleNamespace(total=100, used=10, free=90, percent=10.0),
    )
    monkeypatch.setattr(
        collectors.psutil,
        "sensors_battery",
        lambda: SimpleNamespace(percent=15.0, secsleft=600, power_plugged=False),
    )
    config = HostMonitorConfig()
    memory = MemoryCollector(config).sample(0.0)[0]
    battery = BatteryCollector(config).sample(0.0)[0]
    assert len(memory.alerts) == 2
    assert battery.values["charge.percent"] == 15.0
    assert battery.alerts[0].direction == "low"
    assert memory.summary == "RAM 80.0% (0.0/0.0 GiB), swap 10.0%"
    assert battery.summary == "Battery 15.0%, discharging, 10m remaining"


def test_temperature_uses_hardware_limits_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        SimpleNamespace(label="Package", current=75.0, high=85.0, critical=100.0),
        SimpleNamespace(label="Core 0", current=70.0, high=None, critical=None),
    ]
    monkeypatch.setattr(
        collectors.psutil,
        "sensors_temperatures",
        lambda fahrenheit=False: {
            "coretemp": entries,
            "nvme": [SimpleNamespace(label="Composite", current=40.0, high=80.0, critical=90.0)],
            "dell_ddv": [
                SimpleNamespace(label="Other", current=35.0, high=0.0, critical=None),
            ],
        },
    )
    sample = TemperatureCollector(HostMonitorConfig()).sample(0.0)[0]
    assert sample.values == {
        "cpu_temperature.min_c": 70.0,
        "cpu_temperature.avg_c": 72.5,
        "cpu_temperature.max_c": 75.0,
    }
    assert sample.alerts[0].warn == 85.0
    assert sample.alerts[0].error == 100.0
    assert sample.alerts[1].warn == 80.0
    assert len(sample.alerts) == 2
    assert sample.summary == "CPU temp min/avg/max 70.0/72.5/75.0 °C"


def test_temperature_aggregate_accepts_explicit_cpu_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collectors.psutil,
        "sensors_temperatures",
        lambda fahrenheit=False: {
            "dell_ddv": [
                SimpleNamespace(label="CPU", current=74.0, high=0.0, critical=None),
                SimpleNamespace(label="Other", current=39.0, high=0.0, critical=None),
            ],
        },
    )
    sample = TemperatureCollector(HostMonitorConfig()).sample(0.0)[0]
    assert sample.values["cpu_temperature.min_c"] == 74.0
    assert sample.values["cpu_temperature.avg_c"] == 74.0
    assert sample.values["cpu_temperature.max_c"] == 74.0


def test_temperature_individual_mode_preserves_all_sensors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collectors.psutil,
        "sensors_temperatures",
        lambda fahrenheit=False: {
            "coretemp": [SimpleNamespace(label="Core 0", current=70.0, high=80.0, critical=100.0)],
            "nvme": [SimpleNamespace(label="Composite", current=40.0, high=80.0, critical=90.0)],
        },
    )
    config = HostMonitorConfig(aggregate_cpu_temperatures_only=False)
    sample = TemperatureCollector(config).sample(0.0)[0]
    assert sample.values["sensor.coretemp.Core_0.current_c"] == 70.0
    assert sample.values["sensor.nvme.Composite.current_c"] == 40.0
    assert "cpu_temperature.avg_c" not in sample.values
    assert sample.summary == "2 temperatures, min/avg/max 40.0/55.0/70.0 °C"


def test_filesystem_skips_pseudo_and_unreadable_mounts(monkeypatch: pytest.MonkeyPatch) -> None:
    partitions = [
        SimpleNamespace(mountpoint="/", fstype="ext4"),
        SimpleNamespace(mountpoint="/proc", fstype="proc"),
        SimpleNamespace(mountpoint="/secret", fstype="ext4"),
    ]
    monkeypatch.setattr(collectors.psutil, "disk_partitions", lambda all=False: partitions)

    def disk_usage(path: str):
        if path == "/secret":
            raise PermissionError
        return SimpleNamespace(total=1000, used=900, free=100, percent=90.0)

    monkeypatch.setattr(collectors.psutil, "disk_usage", disk_usage)
    sample = FilesystemCollector(HostMonitorConfig()).sample(0.0)[0]
    assert sample.values["mount.root.percent"] == 90.0
    assert len(sample.alerts) == 1
    assert sample.summary == "1 filesystems, highest / at 90.0%"


def test_disk_io_omits_first_rates_and_clamps_counter_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    io = iter([
        SimpleNamespace(read_bytes=100, write_bytes=200, read_count=10, write_count=20, busy_time=50),
        SimpleNamespace(read_bytes=50, write_bytes=250, read_count=5, write_count=25, busy_time=70),
    ])
    monkeypatch.setattr(collectors.psutil, "disk_io_counters", lambda **kwargs: next(io))
    collector = DiskIoCollector()
    first = collector.sample(1.0)[0]
    second = collector.sample(3.0)[0]
    assert "read.bytes_per_sec" not in first.values
    assert second.values["read.bytes_per_sec"] == 0.0
    assert second.values["write.bytes_per_sec"] == 25.0
    assert second.values["busy.percent"] == 1.0
    assert second.summary == "Disk I/O R 0.0 B/s, W 25.0 B/s, busy 1.0%"


def _net(bytes_recv: int, bytes_sent: int):
    return SimpleNamespace(
        bytes_recv=bytes_recv,
        bytes_sent=bytes_sent,
        packets_recv=10,
        packets_sent=20,
        errin=0,
        errout=0,
        dropin=0,
        dropout=0,
    )


def test_network_rates_filters_and_required_link(monkeypatch: pytest.MonkeyPatch) -> None:
    counters = iter([
        {"eth0": _net(100, 200), "docker0": _net(500, 500)},
        {"eth0": _net(300, 500), "docker0": _net(600, 600)},
    ])
    monkeypatch.setattr(collectors.psutil, "net_io_counters", lambda **kwargs: next(counters))
    monkeypatch.setattr(
        collectors.psutil,
        "net_if_stats",
        lambda: {"eth0": SimpleNamespace(isup=False, speed=1000, mtu=1500)},
    )
    config = HostMonitorConfig(required_interfaces=("eth0",))
    collector = NetworkCollector(config)
    first = collector.sample(1.0)
    second = collector.sample(3.0)
    assert [sample.path for sample in first] == ["network.eth0"]
    assert "rx.bytes_per_sec" not in first[0].values
    assert second[0].values["rx.bytes_per_sec"] == 100.0
    assert second[0].values["link.up"] == 0.0
    assert second[0].alerts[0].direction == "low"
    assert second[0].summary == "eth0 down, RX 100.0 B/s, TX 150.0 B/s"


def test_network_survives_link_stats_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collectors.psutil, "net_io_counters", lambda **kwargs: {"eth0": _net(1, 2)}
    )

    def denied():
        raise PermissionError

    monkeypatch.setattr(collectors.psutil, "net_if_stats", denied)
    sample = NetworkCollector(HostMonitorConfig()).sample(1.0)[0]
    assert sample.values["rx.total_bytes"] == 1.0
    assert "link.up" not in sample.values


def test_nvidia_gpu_collector_parses_output_and_disables_when_missing() -> None:
    result = SimpleNamespace(stdout="0, 75, 1000, 8192, 82\n")
    collector = NvidiaGpuCollector(HostMonitorConfig(), runner=lambda *args, **kwargs: result)
    sample = collector.sample(0.0)[0]
    assert sample.path == "gpu.0"
    assert sample.values["utilization.percent"] == 75.0
    assert sample.alerts[0].value == 82.0
    assert sample.summary == "GPU 0 75.0%, memory 1000/8192 MiB, 82.0 °C"

    calls = 0

    def missing(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise FileNotFoundError

    unsupported = NvidiaGpuCollector(HostMonitorConfig(), runner=missing)
    assert unsupported.sample(0.0) == []
    assert unsupported.sample(1.0) == []
    assert calls == 1


def test_nvidia_gpu_collector_is_timeout_bounded() -> None:
    def timeout(*args, **kwargs):
        raise collectors.subprocess.TimeoutExpired("nvidia-smi", 1.0)

    collector = NvidiaGpuCollector(HostMonitorConfig(), runner=timeout)
    assert collector.sample(0.0) == []


XENOMAI_STAT = """\
CPU  PID    MSW        CSW        XSC        PF    STAT       %CPU  NAME
  0  0      0          5321688352 0          0     00018000   96.8  [ROOT/0]
  1  852    1          1          5          0     000680c0    0.0  latency
  0  900    12         100        20         0     00068042   14.5  control loop
  1  0      0          13288313   0          0     00000000    0.0  [IRQ4355: [timer]]
"""


def test_parse_xenomai_sched_stat_extracts_msw_load_and_skips_irq() -> None:
    entries = parse_xenomai_sched_stat(XENOMAI_STAT)
    assert [(entry.name, entry.msw, entry.load_percent) for entry in entries] == [
        ("[ROOT/0]", 0, 96.8),
        ("latency", 1, 0.0),
        ("control loop", 12, 14.5),
    ]


def test_parse_xenomai_sched_stat_accepts_load_header() -> None:
    text = "CPU PID MSW CSW PF STAT LOAD NAME\n2 42 7 10 0 0000 3.5 worker name\n"
    assert parse_xenomai_sched_stat(text) == [
        collectors.XenomaiSchedEntry(2, 42, 7, 3.5, "worker name")
    ]


def test_xenomai_collector_skips_root_and_uses_stable_name_paths(tmp_path) -> None:
    path = tmp_path / "stat"
    path.write_text(XENOMAI_STAT, encoding="utf-8")
    collector = XenomaiProcCollector(HostMonitorConfig(xenomai_stat_path=str(path)))
    samples = collector.sample(0.0)
    assert [sample.path for sample in samples] == [
        "xenomai.latency",
        "xenomai.control_loop",
    ]
    assert samples[1].values == {"msw.count": 12.0, "load.percent": 14.5}
    assert samples[1].summary == "control loop: load 14.5%, MSW 12 (CPU 0, PID 900)"


def test_xenomai_collector_is_optional_when_proc_file_is_missing(tmp_path) -> None:
    config = HostMonitorConfig(xenomai_stat_path=str(tmp_path / "missing"))
    assert XenomaiProcCollector(config).sample(0.0) == []
