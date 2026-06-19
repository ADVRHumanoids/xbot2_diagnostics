#ifndef XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H
#define XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

namespace XBot::diagnostics {

/// Lock-free, single-producer accumulator for high-rate scalar metrics.
/// Call update() from your kHz loop, flush() from a low-rate diagnostics timer.
class StatAccumulator {

public:

    std::vector<std::string> value_keys = {"duration", "count", "mean", "std_dev", "rms", "min", "max"};

    struct Stats {
        double mean = 0, std_dev = 0, rms = 0, min = 0, max = 0;
        double p05 = 0, p50 = 0, p95 = 0;
        uint64_t count = 0;
        double duration = 0;
        bool has_percentiles = false;

        void to_std_vector(std::vector<double>& vec) const {
            vec.clear();
            vec.push_back(duration);
            vec.push_back(static_cast<double>(count));
            vec.push_back(mean);
            vec.push_back(std_dev);
            vec.push_back(rms);
            vec.push_back(min);
            vec.push_back(max);
            if (has_percentiles) {
                vec.push_back(p05);
                vec.push_back(p50);
                vec.push_back(p95);
            }
        }
    };

    explicit StatAccumulator(std::size_t percentile_capacity = 0)
        : _cap(percentile_capacity)
    {
        if (_cap > 0) {
            _buf.resize(_cap);
            value_keys.push_back("p05");
            value_keys.push_back("p50");
            value_keys.push_back("p95");
        }
    }

    void update(double x) noexcept {
        // Welford's online algorithm — numerically stable, O(1)
        ++_n;
        double delta = x - _mean;
        _mean += delta / _n;
        double delta2 = x - _mean;
        _M2 += delta * delta2;
        _sum_sq += x * x;
        if (x < _min) _min = x;
        if (x > _max) _max = x;
        if (_cap > 0)
            _buf[_write_idx++ % _cap] = x;
    }

    uint64_t count() const noexcept { return _n; }

    Stats flush(uint64_t time_ns = 0) noexcept {
        Stats s;
        s.duration = (time_ns - _initial_time_ns) * 1e-9; // convert to seconds
        s.count  = _n;
        s.mean   = (_n > 0) ? _mean : 0.0;
        s.std_dev= (_n > 1) ? std::sqrt(_M2 / (_n - 1)) : 0.0;
        s.rms    = (_n > 0) ? std::sqrt(_sum_sq / _n) : 0.0;
        s.min    = (_n > 0) ? _min : 0.0;
        s.max    = (_n > 0) ? _max : 0.0;
        if (_cap > 0 && _write_idx > 0) {
            s.has_percentiles = true;
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
        reset(time_ns);
        return s;
    }

private:

    void reset(uint64_t time_ns) noexcept {
        _n = 0; _mean = 0; _M2 = 0; _sum_sq = 0;
        _min =  std::numeric_limits<double>::max();
        _max = -std::numeric_limits<double>::max();
        _write_idx = 0;
        _initial_time_ns = time_ns;
    }

    uint64_t _n{0};
    double _mean{0}, _M2{0}, _sum_sq{0};
    double _min{ std::numeric_limits<double>::max()};
    double _max{-std::numeric_limits<double>::max()};
    std::size_t _cap{0};
    std::size_t _write_idx{0};
    std::vector<double> _buf;
    uint64_t _initial_time_ns{0};
};

} // namespace diag

#endif // XBOT2_DIAGNOSTICS_STATS_ACCUMULATOR_H
