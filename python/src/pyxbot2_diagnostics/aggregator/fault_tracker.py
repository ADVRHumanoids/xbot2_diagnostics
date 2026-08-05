"""Fault lifecycle tracking for normalized health diagnostics messages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class FaultKey:
    """Stable identity for one standardized fault report."""

    hw_id: str
    node: str
    report: str


@dataclass(frozen=True)
class FaultState:
    """Current lifecycle state for one standardized fault report."""

    key: FaultKey
    active: bool
    level: int
    message: str
    first_raised: float
    last_raised: float
    last_cleared: float | None
    occurrence_count: int


@dataclass(frozen=True)
class FaultTransition:
    """A fault state transition emitted by :class:`FaultLifecycleTracker`."""

    kind: str
    state: FaultState
    stamp: float


class FaultLifecycleTracker:
    """Track faults published through the string-native health contract.

    Only nodes whose final path segment is ``health`` participate. A valid
    message contains exactly one ``fault_count`` value and zero or more repeated
    ``fault_report`` values. Reports are the complete active set, not deltas.
    """

    def __init__(self) -> None:
        self.states: dict[FaultKey, FaultState] = {}
        self._active_by_source: dict[tuple[str, str], set[str]] = {}

    def update(self, message: Any, recv_time: float) -> list[FaultTransition]:
        if not self._is_health_node(message.node) or message.level == 3:
            return []

        reports, declared_count, valid = self._extract_reports(message.values)
        if not valid or declared_count != len(reports):
            return []
        if reports and message.level not in (1, 2):
            return []
        if not reports and message.level != 0:
            return []

        source = (message.hw_id or "unknown", message.node)
        previous_reports = set(self._active_by_source.get(source, set()))
        stamp = self._event_stamp(message.stamp, recv_time)
        transitions: list[FaultTransition] = []

        for report in sorted(previous_reports - reports):
            key = FaultKey(source[0], source[1], report)
            previous = self.states[key]
            state = FaultState(
                key=key,
                active=False,
                level=0,
                message=message.msg,
                first_raised=previous.first_raised,
                last_raised=previous.last_raised,
                last_cleared=stamp,
                occurrence_count=previous.occurrence_count,
            )
            self.states[key] = state
            transitions.append(FaultTransition("cleared", state, stamp))

        for report in sorted(reports - previous_reports):
            key = FaultKey(source[0], source[1], report)
            previous = self.states.get(key)
            state = FaultState(
                key=key,
                active=True,
                level=message.level,
                message=message.msg,
                first_raised=stamp if previous is None else previous.first_raised,
                last_raised=stamp,
                last_cleared=None if previous is None else previous.last_cleared,
                occurrence_count=1 if previous is None else previous.occurrence_count + 1,
            )
            self.states[key] = state
            transitions.append(FaultTransition("raised", state, stamp))

        for report in sorted(reports & previous_reports):
            key = FaultKey(source[0], source[1], report)
            previous = self.states[key]
            self.states[key] = FaultState(
                key=key,
                active=True,
                level=message.level,
                message=message.msg,
                first_raised=previous.first_raised,
                last_raised=previous.last_raised,
                last_cleared=previous.last_cleared,
                occurrence_count=previous.occurrence_count,
            )

        self._active_by_source[source] = reports
        return transitions

    @staticmethod
    def _is_health_node(node: Any) -> bool:
        parts = [part for part in str(node).split("/") if part]
        return bool(parts) and parts[-1].lower() == "health"

    @staticmethod
    def _event_stamp(source_stamp: Any, recv_time: float) -> float:
        try:
            stamp = float(source_stamp)
        except (TypeError, ValueError):
            return recv_time
        return stamp if math.isfinite(stamp) and stamp > 0.0 else recv_time

    @classmethod
    def _extract_reports(
        cls, values: Iterable[Any]
    ) -> tuple[set[str], int | None, bool]:
        reports: set[str] = set()
        counts: list[int] = []

        for item in values:
            key = str(item.key).strip().lower()
            if key == "fault_report":
                report = cls._normalize_report(item.value)
                if report is None:
                    return set(), None, False
                reports.add(report)
            elif key == "fault_count":
                try:
                    count = int(str(item.value).strip(), 10)
                except (TypeError, ValueError):
                    return set(), None, False
                if count < 0:
                    return set(), None, False
                counts.append(count)

        if len(counts) != 1:
            return set(), None, False
        return reports, counts[0], True

    @staticmethod
    def _normalize_report(value: Any) -> str | None:
        report = str(value).strip()
        return report if report else None
