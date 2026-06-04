#ifndef XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H
#define XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

namespace XBot::diagnostics {

/// Lock-free, single-producer accumulator for high-rate scalar metrics.
/// Call update() from your kHz loop, flush() from a low-rate diagnostics timer.
class StatAccumulator {

public:

    struct Stats {
        double mean = 0, std_dev = 0, min = 0, max = 0;
        double p05 = 0, p50 = 0, p95 = 0;
        uint64_t count = 0;
    };

    explicit StatAccumulator(std::size_t percentile_capacity = 0)
        : _cap(percentile_capacity)
    {
        if (_cap > 0)
            _buf.resize(_cap);
    }

    void update(double x) noexcept {
        // Welford's online algorithm — numerically stable, O(1)
        ++_n;
        double delta = x - _mean;
        _mean += delta / _n;
        double delta2 = x - _mean;
        _M2 += delta * delta2;
        if (x < _min) _min = x;
        if (x > _max) _max = x;
        if (_cap > 0)
            _buf[_write_idx++ % _cap] = x;
    }

    Stats flush() noexcept {
        Stats s;
        s.count  = _n;
        s.mean   = (_n > 0) ? _mean : 0.0;
        s.std_dev= (_n > 1) ? std::sqrt(_M2 / (_n - 1)) : 0.0;
        s.min    = (_n > 0) ? _min : 0.0;
        s.max    = (_n > 0) ? _max : 0.0;
        if (_cap > 0 && _write_idx > 0) {
            std::size_t n = std::min(_write_idx, _cap);
            auto beg = _buf.begin();
            auto end = beg + static_cast<std::ptrdiff_t>(n);
            std::sort(beg, end); // safe: reset() discards buffer contents anyway
            auto pct = [&](double q) -> double {
                return _buf[static_cast<std::size_t>(q * (n - 1))];
            };
            s.p05 = pct(0.05);
            s.p50 = pct(0.50);
            s.p95 = pct(0.95);
        }
        reset();
        return s;
    }

private:

    void reset() noexcept {
        _n = 0; _mean = 0; _M2 = 0;
        _min =  std::numeric_limits<double>::max();
        _max = -std::numeric_limits<double>::max();
        _write_idx = 0;
    }

    uint64_t _n{0};
    double _mean{0}, _M2{0};
    double _min{ std::numeric_limits<double>::max()};
    double _max{-std::numeric_limits<double>::max()};
    std::size_t _cap{0};
    std::size_t _write_idx{0};
    std::vector<double> _buf;
};

} // namespace diag

#endif // XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H