import math
from dataclasses import dataclass


@dataclass
class Stats:
    mean:    float = 0.0
    std_dev: float = 0.0
    min:     float = 0.0
    max:     float = 0.0
    p05:     float = 0.0
    p50:     float = 0.0
    p95:     float = 0.0
    count:   int   = 0


class StatAccumulator:
    """Single-producer accumulator mirroring the C++ StatAccumulator.

    Call update() from your high-rate loop, flush() from a low-rate timer.
    Pass percentile_capacity > 0 to enable p05 / p50 / p95 estimation via a
    ring buffer of the newest N samples (sorted at flush time).
    """

    def __init__(self, percentile_capacity: int = 0) -> None:
        self._cap = percentile_capacity
        self._buf: list[float] = [0.0] * percentile_capacity if percentile_capacity > 0 else []
        self._reset()

    # ------------------------------------------------------------------ #

    def update(self, x: float) -> None:
        # Welford's online algorithm — numerically stable, O(1)
        self._n += 1
        delta = x - self._mean
        self._mean += delta / self._n
        self._M2 += delta * (x - self._mean)
        if x < self._min:
            self._min = x
        if x > self._max:
            self._max = x
        if self._cap > 0:
            self._buf[self._write_idx % self._cap] = x
            self._write_idx += 1

    def flush(self) -> Stats:
        s = Stats()
        s.count = self._n
        if self._n > 0:
            s.mean    = self._mean
            s.std_dev = math.sqrt(self._M2 / (self._n - 1)) if self._n > 1 else 0.0
            s.min     = self._min
            s.max     = self._max
        if self._cap > 0 and self._write_idx > 0:
            n = min(self._write_idx, self._cap)
            tmp = sorted(self._buf[:n])  # sort in-place on a slice copy
            def _pct(q: float) -> float:
                return tmp[int(q * (n - 1))]
            s.p05 = _pct(0.05)
            s.p50 = _pct(0.50)
            s.p95 = _pct(0.95)
        self._reset()
        return s

    # ------------------------------------------------------------------ #

    def _reset(self) -> None:
        self._n:          int   = 0
        self._mean:       float = 0.0
        self._M2:         float = 0.0
        self._min:        float = math.inf
        self._max:        float = -math.inf
        self._write_idx:  int   = 0
