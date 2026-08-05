from __future__ import annotations

from dataclasses import dataclass, field

from pyxbot2_diagnostics.aggregator.aggregator import (
    DiagnosticKeyValue,
    DiagnosticsAggregator,
    DiagnosticsMessage,
)
from pyxbot2_diagnostics.aggregator.config import (
    AggregatorConfig,
    AggregatorSection,
    SinksSection,
)
from pyxbot2_diagnostics.aggregator.fault_tracker import FaultLifecycleTracker


def _values(*reports: str, count: int | None = None) -> tuple[DiagnosticKeyValue, ...]:
    declared_count = len(reports) if count is None else count
    return (
        DiagnosticKeyValue("fault_count", str(declared_count)),
        *(DiagnosticKeyValue("fault_report", report) for report in reports),
    )


def _message(
    *,
    level: int,
    values: tuple[DiagnosticKeyValue, ...],
    stamp: float = 10.0,
    msg: str = "fault",
    node: str = "/xbot/joint/knee_pitch_1/health",
) -> DiagnosticsMessage:
    return DiagnosticsMessage(
        v=1,
        node=node,
        hw_id="knee_pitch_1",
        stamp=stamp,
        level=level,
        msg=msg,
        values=values,
    )


def test_single_fault_raise_duplicate_and_clear() -> None:
    tracker = FaultLifecycleTracker()
    fault = _message(level=2, values=_values("motor over temperature"))

    transitions = tracker.update(fault, recv_time=11.0)
    assert [item.kind for item in transitions] == ["raised"]
    state = transitions[0].state
    assert state.key.report == "motor over temperature"
    assert state.active
    assert state.last_raised == 10.0
    assert state.occurrence_count == 1

    assert tracker.update(fault, recv_time=12.0) == []
    assert next(iter(tracker.states.values())).occurrence_count == 1

    cleared = tracker.update(
        _message(level=0, values=_values(), stamp=20.0, msg="OK"),
        recv_time=21.0,
    )
    assert [item.kind for item in cleared] == ["cleared"]
    assert not cleared[0].state.active
    assert cleared[0].state.last_cleared == 20.0


def test_report_change_clears_old_and_raises_new() -> None:
    tracker = FaultLifecycleTracker()
    tracker.update(
        _message(level=2, values=_values("motor over temperature")),
        recv_time=10.0,
    )

    transitions = tracker.update(
        _message(
            level=2,
            stamp=30.0,
            values=_values("encoder signal lost"),
        ),
        recv_time=31.0,
    )

    assert [(item.kind, item.state.key.report) for item in transitions] == [
        ("cleared", "motor over temperature"),
        ("raised", "encoder signal lost"),
    ]


def test_multiple_simultaneous_faults_are_diffed_as_sets() -> None:
    tracker = FaultLifecycleTracker()
    first = tracker.update(
        _message(
            level=2,
            values=_values("motor over temperature", "encoder signal lost"),
        ),
        recv_time=10.0,
    )
    assert {(item.kind, item.state.key.report) for item in first} == {
        ("raised", "motor over temperature"),
        ("raised", "encoder signal lost"),
    }

    second = tracker.update(
        _message(
            level=2,
            stamp=40.0,
            values=_values("encoder signal lost", "dc link over voltage"),
        ),
        recv_time=41.0,
    )
    assert {(item.kind, item.state.key.report) for item in second} == {
        ("cleared", "motor over temperature"),
        ("raised", "dc link over voltage"),
    }


def test_stale_does_not_clear_hardware_faults() -> None:
    tracker = FaultLifecycleTracker()
    tracker.update(
        _message(level=2, values=_values("motor over temperature")),
        recv_time=10.0,
    )

    assert tracker.update(
        _message(
            level=3,
            stamp=50.0,
            msg="STALE",
            values=_values("motor over temperature"),
        ),
        recv_time=50.0,
    ) == []
    assert next(iter(tracker.states.values())).active


def test_non_health_message_is_not_interpreted_as_fault_contract() -> None:
    tracker = FaultLifecycleTracker()
    transitions = tracker.update(
        _message(
            level=2,
            node="/xbot/joint/knee_pitch_1/temperature",
            values=_values("motor over temperature"),
        ),
        recv_time=10.0,
    )
    assert transitions == []
    assert tracker.states == {}


def test_health_message_requires_exactly_one_fault_count() -> None:
    tracker = FaultLifecycleTracker()
    missing = _message(
        level=2,
        values=(DiagnosticKeyValue("fault_report", "motor over temperature"),),
    )
    duplicate = _message(
        level=2,
        values=(
            DiagnosticKeyValue("fault_count", "1"),
            DiagnosticKeyValue("fault_count", "1"),
            DiagnosticKeyValue("fault_report", "motor over temperature"),
        ),
    )
    assert tracker.update(missing, recv_time=10.0) == []
    assert tracker.update(duplicate, recv_time=10.0) == []
    assert tracker.states == {}


def test_fault_count_must_match_unique_reports() -> None:
    tracker = FaultLifecycleTracker()
    assert tracker.update(
        _message(level=2, values=_values("motor over temperature", count=2)),
        recv_time=10.0,
    ) == []
    assert tracker.states == {}


def test_inconsistent_level_and_reports_is_ignored() -> None:
    tracker = FaultLifecycleTracker()
    assert tracker.update(
        _message(level=0, values=_values("motor over temperature")),
        recv_time=10.0,
    ) == []
    assert tracker.update(
        _message(level=2, values=_values()),
        recv_time=10.0,
    ) == []
    assert tracker.states == {}


class NullSource:
    def poll(self, timeout_ms: int = 100):
        del timeout_ms
        return []

    def close(self) -> None:
        return


@dataclass
class FaultSink:
    calls: list[str] = field(default_factory=list)
    transitions: list[object] = field(default_factory=list)
    state_snapshots: list[dict[object, object]] = field(default_factory=list)

    def handle_message(self, message) -> None:
        del message
        self.calls.append("message")

    def handle_fault_transitions(self, transitions, states) -> None:
        self.calls.append("transitions")
        self.transitions.extend(transitions)
        self.state_snapshots.append(dict(states))

    def publish_state(self, states) -> None:
        del states

    def close(self) -> None:
        return


def test_aggregator_publishes_transitions_before_health_snapshot() -> None:
    config = AggregatorConfig(
        aggregator=AggregatorSection(
            zmq_endpoint="inproc://unused",
            stale_timeout_sec=5.0,
            stale_check_interval_sec=1.0,
        ),
        sinks=SinksSection(),
    )
    sink = FaultSink()
    aggregator = DiagnosticsAggregator(config, [sink], sources=[NullSource()])

    aggregator.process_message(
        _message(level=2, values=_values("motor over temperature")),
        now=11.0,
    )

    assert sink.calls[:2] == ["transitions", "message"]
    assert len(sink.transitions) == 1
    assert sink.transitions[0].kind == "raised"
    assert next(iter(aggregator.fault_states.values())).active
    aggregator.close()
