#include <chrono>
#include <cstdio>
#include <random>
#include <vector>

#include "xbot2_diagnostics/stats_accumulator.h"

using namespace XBot::diagnostics;
using Clock = std::chrono::steady_clock;
using ns    = std::chrono::nanoseconds;

static double elapsed_ns(Clock::time_point t0, Clock::time_point t1)
{
    return static_cast<double>(std::chrono::duration_cast<ns>(t1 - t0).count());
}

struct Result {
    double update_ns;   // mean cost per update() call
    double flush_us;    // mean cost per flush() call
};

Result bench(std::size_t capacity, std::size_t updates_per_flush,
             std::size_t flushes, const std::vector<double>& data)
{
    StatAccumulator acc(capacity);
    std::size_t idx = 0;

    double total_update_ns = 0.0;
    double total_flush_ns  = 0.0;

    for (std::size_t f = 0; f < flushes; ++f) {
        auto t0 = Clock::now();
        for (std::size_t u = 0; u < updates_per_flush; ++u)
            acc.update(data[idx++]);
        auto t1 = Clock::now();
        acc.flush();
        auto t2 = Clock::now();

        total_update_ns += elapsed_ns(t0, t1);
        total_flush_ns  += elapsed_ns(t1, t2);
    }

    return {
        total_update_ns / static_cast<double>(flushes * updates_per_flush),
        total_flush_ns  / static_cast<double>(flushes) / 1e3,
    };
}

int main()
{
    constexpr std::size_t FLUSHES          = 500;
    constexpr std::size_t UPDATES_PER_FLUSH = 10'000;
    constexpr std::size_t TOTAL            = FLUSHES * UPDATES_PER_FLUSH;

    // pre-generate random data so the RNG doesn't pollute measurements
    std::mt19937_64 rng(42);
    std::normal_distribution<double> dist(0.0, 1.0);
    std::vector<double> data(TOTAL);
    for (auto& v : data) v = dist(rng);

    std::printf("%-14s  %9s  %8s  %9s  %9s  %9s\n",
                "capacity", "update ns", "flush µs",
                "p05", "p50", "p95");
    std::printf("%s\n", std::string(66, '-').c_str());

    // sample one final flush for sanity-check values
    auto run_and_print = [&](std::size_t cap) {
        auto r = bench(cap, UPDATES_PER_FLUSH, FLUSHES, data);

        // one more flush for display values
        StatAccumulator acc2(cap);
        for (std::size_t i = 0; i < UPDATES_PER_FLUSH; ++i)
            acc2.update(data[i]);
        auto s = acc2.flush();

        std::printf("%-14zu  %9.2f  %8.2f  %9.4f  %9.4f  %9.4f\n",
                    cap, r.update_ns, r.flush_us,
                    s.p05, s.p50, s.p95);
    };

    run_and_print(0);       // percentiles disabled — baseline
    run_and_print(128);
    run_and_print(256);
    run_and_print(1024);
    run_and_print(4096);
    run_and_print(16384);

    return 0;
}
