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
    fault = _message(
        level=2,
        values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),),
    )

    transitions = tracker.update(fault, recv_time=11.0)
    assert [item.kind for item in transitions] == ["raised"]
    state = transitions[0].state
    assert state.key.code == "0x4210"
    assert state.active
    assert state.last_raised == 10.0
    assert state.occurrence_count == 1

    assert tracker.update(fault, recv_time=12.0) == []
    assert next(iter(tracker.states.values())).occurrence_count == 1

    cleared = tracker.update(
        _message(
            level=0,
            values=(DiagnosticKeyValue("fault_codes", []),),
            stamp=20.0,
            msg="OK",
        ),
        recv_time=21.0,
    )
    assert [item.kind for item in cleared] == ["cleared"]
    assert not cleared[0].state.active
    assert cleared[0].state.last_cleared == 20.0


def test_code_change_clears_old_and_raises_new() -> None:
    tracker = FaultLifecycleTracker()
    tracker.update(
        _message(level=2, values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),)),
        recv_time=10.0,
    )

    transitions = tracker.update(
        _message(
            level=2,
            stamp=30.0,
            values=(DiagnosticKeyValue("fault_codes", ["0x7500"]),),
        ),
        recv_time=31.0,
    )

    assert [(item.kind, item.state.key.code) for item in transitions] == [
        ("cleared", "0x4210"),
        ("raised", "0x7500"),
    ]


def test_multiple_simultaneous_faults_are_diffed_as_sets() -> None:
    tracker = FaultLifecycleTracker()
    first = tracker.update(
        _message(
            level=2,
            values=(DiagnosticKeyValue("fault_codes", [0x4210, "0x7500"]),),
        ),
        recv_time=10.0,
    )
    assert {(item.kind, item.state.key.code) for item in first} == {
        ("raised", "0x4210"),
        ("raised", "0x7500"),
    }

    second = tracker.update(
        _message(
            level=2,
            stamp=40.0,
            values=(DiagnosticKeyValue("fault_codes", ["0x7500", "0x8611"]),),
        ),
        recv_time=41.0,
    )
    assert {(item.kind, item.state.key.code) for item in second} == {
        ("cleared", "0x4210"),
        ("raised", "0x8611"),
    }


def test_stale_does_not_clear_hardware_faults() -> None:
    tracker = FaultLifecycleTracker()
    tracker.update(
        _message(level=2, values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),)),
        recv_time=10.0,
    )

    assert tracker.update(
        _message(
            level=3,
            stamp=50.0,
            msg="STALE",
            values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),),
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
            values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),),
        ),
        recv_time=10.0,
    )
    assert transitions == []
    assert tracker.states == {}


def test_health_message_requires_fault_codes_key() -> None:
    tracker = FaultLifecycleTracker()
    transitions = tracker.update(
        _message(level=2, values=(DiagnosticKeyValue("temperature", 90.0),)),
        recv_time=10.0,
    )
    assert transitions == []
    assert tracker.states == {}


def test_inconsistent_level_and_fault_codes_is_ignored() -> None:
    tracker = FaultLifecycleTracker()
    assert tracker.update(
        _message(level=0, values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),)),
        recv_time=10.0,
    ) == []
    assert tracker.update(
        _message(level=2, values=(DiagnosticKeyValue("fault_codes", []),)),
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
    transitions: list[object] = field(default_factory=list)
    state_snapshots: list[dict[object, object]] = field(default_factory=list)

    def handle_message(self, message) -> None:
        del message

    def handle_fault_transitions(self, transitions, states) -> None:
        self.transitions.extend(transitions)
        self.state_snapshots.append(dict(states))

    def publish_state(self, states) -> None:
        del states

    def close(self) -> None:
        return


def test_aggregator_publishes_fault_transitions_to_opt_in_sink() -> None:
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
        _message(level=2, values=(DiagnosticKeyValue("fault_codes", ["0x4210"]),)),
        now=11.0,
    )

    assert len(sink.transitions) == 1
    assert sink.transitions[0].kind == "raised"
    assert next(iter(aggregator.fault_states.values())).active
    aggregator.close()
