from pyxbot2_diagnostics.aggregator.aggregator import (
    DiagnosticKeyValue,
    DiagnosticsMessage,
)
from pyxbot2_diagnostics.aggregator.fault_tracker import (
    FaultKey,
    FaultState,
    FaultTransition,
)
from pyxbot2_diagnostics.aggregator.sinks.influxdb_sink import InfluxDBSink


class FakeWriteApi:
    def __init__(self) -> None:
        self.calls = []

    def write(self, *, bucket, org, record):
        self.calls.append({"bucket": bucket, "org": org, "record": record})


def _sink():
    fake = FakeWriteApi()
    sink = InfluxDBSink(
        enabled=True,
        url="",
        token="",
        org="xbot2",
        bucket="diagnostics",
        write_api=fake,
    )
    return sink, fake


def _health(*reports: str, level: int = 2, stamp: float = 10.0):
    values = [DiagnosticKeyValue("fault_count", str(len(reports)))]
    values.extend(DiagnosticKeyValue("fault_report", report) for report in reports)
    return DiagnosticsMessage(
        v=1,
        node="/xbot/joint/knee_pitch_1/health",
        hw_id="knee_pitch_1",
        stamp=stamp,
        level=level,
        msg="Drive faults active" if reports else "OK",
        values=tuple(values),
    )


def test_health_snapshot_is_one_grafana_row_per_source() -> None:
    sink, fake = _sink()
    sink.handle_message(
        _health("motor over temperature", "encoder signal lost")
    )
    sink._last_flush = 0.0
    sink.publish_state({})

    point = fake.calls[0]["record"][0]
    assert point["measurement"] == "health"
    assert point["tags"] == {
        "hw_id": "knee_pitch_1",
        "path": "/xbot/joint/knee_pitch_1/health",
        "name": "knee_pitch_1",
        "component": "xbot/joint",
    }
    assert point["fields"]["fault_count"] == 2
    assert point["fields"]["active_fault_reports"] == (
        "encoder signal lost; motor over temperature"
    )
    assert point["time"] == 10_000_000_000


def test_fault_event_uses_standardized_report_as_tag() -> None:
    sink, fake = _sink()
    state = FaultState(
        key=FaultKey(
            "knee_pitch_1",
            "/xbot/joint/knee_pitch_1/health",
            "motor over temperature",
        ),
        active=True,
        level=2,
        message="Drive faults active",
        first_raised=10.0,
        last_raised=10.0,
        last_cleared=None,
        occurrence_count=1,
    )
    sink.handle_fault_transitions(
        [FaultTransition("raised", state, 10.0)],
        {state.key: state},
    )
    sink.handle_message(_health("motor over temperature"))
    sink._last_flush = 0.0
    sink.publish_state({})

    event, health = fake.calls[0]["record"]
    assert event["measurement"] == "fault_event"
    assert event["tags"]["fault_report"] == "motor over temperature"
    assert event["tags"]["transition"] == "raised"
    assert event["fields"]["active"] is True
    assert health["fields"]["last_fault_report"] == "motor over temperature"
    assert health["fields"]["last_fault_active"] is True
    assert health["fields"]["last_raised_ns"] == 10_000_000_000


def test_clear_event_updates_next_health_snapshot() -> None:
    sink, fake = _sink()
    state = FaultState(
        key=FaultKey(
            "knee_pitch_1",
            "/xbot/joint/knee_pitch_1/health",
            "motor over temperature",
        ),
        active=False,
        level=0,
        message="OK",
        first_raised=10.0,
        last_raised=10.0,
        last_cleared=20.0,
        occurrence_count=1,
    )
    sink.handle_fault_transitions(
        [FaultTransition("cleared", state, 20.0)],
        {state.key: state},
    )
    sink.handle_message(_health(level=0, stamp=20.0))
    sink._last_flush = 0.0
    sink.publish_state({})

    event, health = fake.calls[0]["record"]
    assert event["tags"]["transition"] == "cleared"
    assert event["fields"]["last_cleared_ns"] == 20_000_000_000
    assert health["fields"]["last_fault_active"] is False
    assert health["fields"]["last_cleared_ns"] == 20_000_000_000
