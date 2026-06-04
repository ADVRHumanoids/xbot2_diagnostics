import json

from aggregator.aggregator import DiagnosticKeyValue, DiagnosticsMessage
from aggregator.sinks.influxdb_sink import InfluxDBSink
from aggregator.sinks.json_file_sink import JsonFileSink
from aggregator.sinks.ros_diagnostics_sink import RosDiagnosticsSink


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


def _msg(node: str = "n1") -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id="hw",
        stamp=1.23,
        level=0,
        msg="OK",
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

    assert len(fake.calls) == 1
    records = fake.calls[0]["record"]
    assert len(records) == 2
    assert records[0]["tags"]["node"] == "n1"
    assert records[0]["tags"]["stat"] in {"mean", "max"}


def test_ros_sink_mapping_and_publish_rate() -> None:
    clock = FakeClock(10.0)
    published = []

    sink = RosDiagnosticsSink(
        publish_rate_hz=1.0,
        publisher=published.append,
        time_fn=clock,
    )

    sink.publish_state({"n1": _msg("n1")})
    sink.publish_state({"n1": _msg("n1")})
    assert len(published) == 1

    clock.advance(1.1)
    sink.publish_state({"n1": _msg("n1")})
    assert len(published) == 2

    status = published[0].status[0]
    assert status.name == "n1"
    assert status.hardware_id == "hw"
    assert status.values[0].key == "torque_error.mean"


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
