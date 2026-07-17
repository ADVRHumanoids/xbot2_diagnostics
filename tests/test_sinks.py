import json

from pyxbot2_diagnostics.aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage
from pyxbot2_diagnostics.aggregator.sinks.influxdb_sink import InfluxDBSink
from pyxbot2_diagnostics.aggregator.sinks.json_file_sink import JsonFileSink
from pyxbot2_diagnostics.aggregator.sinks.ros_diagnostics_sink import RosDiagnosticsSink


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


class FakeWriteApi:
    def __init__(self) -> None:
        self.calls = []

    def write(self, *, bucket, org, record):
        self.calls.append({"bucket": bucket, "org": org, "record": record})


def _level_value(level):
    if isinstance(level, bytes):
        return level[0]
    return level


def _msg(node: str = "n1", *, level: int = 0, msg: str = "OK") -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id="hw",
        stamp=1.23,
        level=level,
        msg=msg,
        values=(
            DiagnosticKeyValue("torque_error.mean", 0.1),
            DiagnosticKeyValue("torque_error.max", 0.3),
            DiagnosticKeyValue("text", "hello"),
        ),
    )


def test_influxdb_sink_writes_points() -> None:
    fake = FakeWriteApi()
    sink = InfluxDBSink(
        enabled=True,
        url="",
        token="",
        org="xbot2",
        bucket="diagnostics",
        write_api=fake,
    )
    sink.handle_message(_msg())
    # Points are buffered; force a flush by advancing past the flush interval.
    sink._last_flush = 0.0
    sink.publish_state({})

    assert len(fake.calls) == 1
    points = fake.calls[0]["record"]
    assert len(points) == 1
    point = points[0]
    assert point["measurement"] == "robot_diagnostics"
    assert point["tags"]["hw_id"] == "hw"
    assert point["tags"]["path"] == "n1"
    assert point["tags"]["name"] == "n1"
    # numeric kv-pairs are individual fields
    assert point["fields"]["torque_error.mean"] == 0.1
    assert point["fields"]["torque_error.max"] == 0.3
    # non-numeric values fall back to string fields
    assert point["fields"]["text"] == "hello"
    assert point["fields"]["level"] == 0


def test_ros_sink_publishes_aggregated_output() -> None:
    aggregated_published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=aggregated_published.append,
        time_fn=FakeClock(10.0),
    )

    sink.publish_state(
        {
            "/xbot/thread/rt_main/load": _msg("/xbot/thread/rt_main/load"),
            "/xbot/thread/nrt_main/load": _msg("/xbot/thread/nrt_main/load", level=1, msg="WARN"),
        }
    )

    statuses = {status.name: status for status in aggregated_published[0].status}
    assert list(statuses) == sorted(statuses)
    assert set(statuses) == {
        "/Robot",
        "/Robot/xbot",
        "/Robot/xbot/thread",
        "/Robot/xbot/thread/nrt_main",
        "/Robot/xbot/thread/nrt_main/load",
        "/Robot/xbot/thread/rt_main",
        "/Robot/xbot/thread/rt_main/load",
    }
    assert _level_value(statuses["/Robot"].level) == 1
    assert statuses["/Robot"].message == "WARN"
    assert _level_value(statuses["/Robot/xbot/thread"].level) == 1
    assert statuses["/Robot/xbot/thread/nrt_main/load"].message == "WARN"
    assert statuses["/Robot/xbot/thread/nrt_main/load"].hardware_id == "hw"
    assert statuses["/Robot/xbot/thread/nrt_main/load"].values[0].key == "torque_error.mean"


def test_ros_sink_does_not_duplicate_robot_root_and_preserves_raw_segments() -> None:
    aggregated_published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=aggregated_published.append,
        time_fn=FakeClock(10.0),
    )

    sink.publish_state({"/Robot/xbot/thread/rt_main": _msg("/Robot/xbot/thread/rt_main")})

    names = [status.name for status in aggregated_published[0].status]
    assert "/Robot/Robot/xbot/thread/rt_main" not in names
    assert "/Robot/xbot/thread/rt_main" in names
    assert "/Robot/xbot/thread/rt_main".split("/")[-1] == "rt_main"


def test_ros_sink_group_levels_use_worst_descendant() -> None:
    aggregated_published = []
    sink = RosDiagnosticsSink(
        aggregated_publisher=aggregated_published.append,
        time_fn=FakeClock(10.0),
    )

    sink.publish_state(
        {
            "/xbot/a": _msg("/xbot/a", level=0),
            "/xbot/b": _msg("/xbot/b", level=2, msg="ERROR"),
            "/other/c": _msg("/other/c", level=3, msg="STALE"),
        }
    )

    statuses = {status.name: status for status in aggregated_published[0].status}
    assert _level_value(statuses["/Robot/xbot"].level) == 2
    assert statuses["/Robot/xbot"].message == "ERROR"
    assert _level_value(statuses["/Robot"].level) == 3
    assert statuses["/Robot"].message == "STALE"


def test_json_file_sink_appends_jsonl(tmp_path) -> None:
    path = tmp_path / "diag.jsonl"
    sink = JsonFileSink(str(path), max_file_size_mb=10)

    sink.handle_message(_msg())
    sink.handle_message(_msg("n2"))

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["node"] == "n1"
    assert payload["values"][0]["key"] == "torque_error.mean"
