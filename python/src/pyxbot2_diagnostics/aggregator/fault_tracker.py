"""Fault lifecycle tracking for normalized diagnostics messages."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


_SINGLE_CODE_KEYS = {"fault_code", "error_code"}
_MULTI_CODE_KEYS = {
    "fault_codes",
    "error_codes",
    "active_fault_codes",
    "active_error_codes",
}
_ACTIVE_KEYS = {"fault_active", "error_active"}


@dataclass(frozen=True)
class FaultKey:
    """Stable identity for one device fault."""

    hw_id: str
    node: str
    code: str


@dataclass(frozen=True)
class FaultState:
    """Current lifecycle state for one device fault."""

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
    """Track raise/clear transitions for diagnostic error codes.

    The tracker recognizes the value keys ``fault_code`` and ``error_code`` for
    a single code, and their plural/``active_*`` variants for multiple active
    codes. Diagnostic levels 1 and 2 imply active faults, level 0 clears the
    current source faults, and level 3 (STALE) deliberately leaves hardware
    fault state unchanged.
    """

    def __init__(self) -> None:
        self.states: dict[FaultKey, FaultState] = {}
        self._active_by_source: dict[tuple[str, str], set[str]] = {}

    def update(self, message: Any, recv_time: float) -> list[FaultTransition]:
        """Consume one normalized diagnostics message and return transitions."""
        if message.level == 3:
            return []

        source = (message.hw_id or "unknown", message.node)
        codes, explicit_active = self._extract_codes(message.values)
        previous_codes = set(self._active_by_source.get(source, set()))

        if explicit_active is False or message.level == 0:
            current_codes: set[str] = set()
        elif explicit_active is True or message.level in (1, 2):
            current_codes = codes
        else:
            current_codes = previous_codes

        # Messages without a recognizable code cannot create a stable fault
        # identity. They can still clear previously active coded faults on OK.
        if not codes and message.level in (1, 2):
            return []

        stamp = self._event_stamp(message.stamp, recv_time)
        transitions: list[FaultTransition] = []

        for code in sorted(previous_codes - current_codes):
            key = FaultKey(source[0], source[1], code)
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

        for code in sorted(current_codes - previous_codes):
            key = FaultKey(source[0], source[1], code)
            previous = self.states.get(key)
            count = 1 if previous is None else previous.occurrence_count + 1
            first_raised = stamp if previous is None else previous.first_raised
            last_cleared = None if previous is None else previous.last_cleared
            state = FaultState(
                key=key,
                active=True,
                level=message.level,
                message=message.msg,
                first_raised=first_raised,
                last_raised=stamp,
                last_cleared=last_cleared,
                occurrence_count=count,
            )
            self.states[key] = state
            transitions.append(FaultTransition("raised", state, stamp))

        for code in sorted(current_codes & previous_codes):
            key = FaultKey(source[0], source[1], code)
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

        self._active_by_source[source] = current_codes
        return transitions

    @staticmethod
    def _event_stamp(source_stamp: Any, recv_time: float) -> float:
        try:
            stamp = float(source_stamp)
        except (TypeError, ValueError):
            return recv_time
        return stamp if math.isfinite(stamp) and stamp > 0.0 else recv_time

    @classmethod
    def _extract_codes(cls, values: Iterable[Any]) -> tuple[set[str], bool | None]:
        codes: set[str] = set()
        explicit_active: bool | None = None

        for item in values:
            key = str(item.key).strip().lower()
            value = item.value
            if key in _SINGLE_CODE_KEYS:
                code = cls._normalize_code(value)
                if code is not None:
                    codes.add(code)
            elif key in _MULTI_CODE_KEYS:
                for raw_code in cls._iter_codes(value):
                    code = cls._normalize_code(raw_code)
                    if code is not None:
                        codes.add(code)
            elif key in _ACTIVE_KEYS:
                explicit_active = cls._as_bool(value)

        return codes, explicit_active

    @staticmethod
    def _iter_codes(value: Any) -> Iterable[Any]:
        if isinstance(value, (list, tuple, set)):
            return value
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value]

    @staticmethod
    def _normalize_code(value: Any) -> str | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            if value == 0:
                return None
            return f"0x{value:X}"
        if isinstance(value, float) and value.is_integer():
            integer = int(value)
            if integer == 0:
                return None
            return f"0x{integer:X}"

        text = str(value).strip()
        if not text or text.lower() in {"0", "0x0", "0x0000", "none", "ok"}:
            return None
        if text.lower().startswith("0x"):
            try:
                return f"0x{int(text, 16):X}"
            except ValueError:
                pass
        return text

    @staticmethod
    def _as_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "on", "1"}:
                return True
            if normalized in {"false", "no", "off", "0"}:
                return False
        return None
