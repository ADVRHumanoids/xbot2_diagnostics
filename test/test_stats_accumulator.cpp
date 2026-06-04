#include <gtest/gtest.h>
#include <cmath>
#include <numeric>

#include "xbot2_diagnostics/stats_accumulator.h"

using XBot::diagnostics::StatAccumulator;

// ── helpers ────────────────────────────────────────────────────────────────

static constexpr double kEps = 1e-9;

// ── no-capacity (legacy) behaviour ─────────────────────────────────────────

TEST(StatAccumulator, DefaultConstructionYieldsZeroStats)
{
    StatAccumulator acc;
    auto s = acc.flush();
    EXPECT_EQ(s.count, 0u);
    EXPECT_DOUBLE_EQ(s.mean,    0.0);
    EXPECT_DOUBLE_EQ(s.std_dev, 0.0);
    EXPECT_DOUBLE_EQ(s.min,     0.0);
    EXPECT_DOUBLE_EQ(s.max,     0.0);
    EXPECT_DOUBLE_EQ(s.p05,     0.0);
    EXPECT_DOUBLE_EQ(s.p50,     0.0);
    EXPECT_DOUBLE_EQ(s.p95,     0.0);
}

TEST(StatAccumulator, MeanStdMinMaxNoCapacity)
{
    StatAccumulator acc; // capacity = 0
    // push values 1..10
    for (int i = 1; i <= 10; ++i) acc.update(static_cast<double>(i));
    auto s = acc.flush();

    EXPECT_EQ(s.count, 10u);
    EXPECT_NEAR(s.mean,    5.5, kEps);
    EXPECT_NEAR(s.std_dev, std::sqrt(82.5 / 9.0), kEps); // sample std
    EXPECT_DOUBLE_EQ(s.min, 1.0);
    EXPECT_DOUBLE_EQ(s.max, 10.0);
    // percentile fields must stay 0 when feature is disabled
    EXPECT_DOUBLE_EQ(s.p05, 0.0);
    EXPECT_DOUBLE_EQ(s.p50, 0.0);
    EXPECT_DOUBLE_EQ(s.p95, 0.0);
}

TEST(StatAccumulator, FlushResetsState)
{
    StatAccumulator acc;
    acc.update(42.0);
    acc.flush();
    auto s = acc.flush(); // second flush after reset
    EXPECT_EQ(s.count, 0u);
    EXPECT_DOUBLE_EQ(s.mean, 0.0);
}

// ── percentile feature ─────────────────────────────────────────────────────

TEST(StatAccumulatorPercentile, SingleSample)
{
    StatAccumulator acc(100);
    acc.update(7.0);
    auto s = acc.flush();
    EXPECT_EQ(s.count, 1u);
    // with n=1 all quantiles index slot 0
    EXPECT_DOUBLE_EQ(s.p05, 7.0);
    EXPECT_DOUBLE_EQ(s.p50, 7.0);
    EXPECT_DOUBLE_EQ(s.p95, 7.0);
}

TEST(StatAccumulatorPercentile, UniformDistributionSmall)
{
    // Values 0,1,...,99 — exact percentiles are well-defined
    StatAccumulator acc(200);
    for (int i = 0; i < 100; ++i) acc.update(static_cast<double>(i));
    auto s = acc.flush();

    EXPECT_EQ(s.count, 100u);
    // floor(0.05 * 99) = 4  → value 4
    EXPECT_DOUBLE_EQ(s.p05, 4.0);
    // floor(0.50 * 99) = 49 → value 49
    EXPECT_DOUBLE_EQ(s.p50, 49.0);
    // floor(0.95 * 99) = 94 → value 94
    EXPECT_DOUBLE_EQ(s.p95, 94.0);
}

TEST(StatAccumulatorPercentile, FlushResetsPercentileState)
{
    StatAccumulator acc(50);
    for (int i = 0; i < 50; ++i) acc.update(static_cast<double>(i));
    acc.flush();

    // After flush/reset, second flush with no new updates must give zeros
    auto s = acc.flush();
    EXPECT_EQ(s.count, 0u);
    EXPECT_DOUBLE_EQ(s.p05, 0.0);
    EXPECT_DOUBLE_EQ(s.p50, 0.0);
    EXPECT_DOUBLE_EQ(s.p95, 0.0);
}

TEST(StatAccumulatorPercentile, PartialFill)
{
    // capacity=1000, only 3 updates — must use n=3, not n=1000
    StatAccumulator acc(1000);
    acc.update(1.0);
    acc.update(2.0);
    acc.update(3.0);
    auto s = acc.flush();

    EXPECT_EQ(s.count, 3u);
    // floor(0.05*2)=0 → 1.0;  floor(0.50*2)=1 → 2.0;  floor(0.95*2)=1 → 2.0
    EXPECT_DOUBLE_EQ(s.p05, 1.0);
    EXPECT_DOUBLE_EQ(s.p50, 2.0);
    EXPECT_DOUBLE_EQ(s.p95, 2.0);
}

TEST(StatAccumulatorPercentile, RingWrap)
{
    // capacity=100, push 200 values (ring wraps once)
    // newest 100 values are 100..199; p05/p50/p95 must reflect that range
    StatAccumulator acc(100);
    for (int i = 0; i < 200; ++i) acc.update(static_cast<double>(i));
    auto s = acc.flush();

    EXPECT_EQ(s.count, 200u); // Welford tracks all 200
    // sorted newest 100: 100,101,...,199
    // floor(0.05*99)=4  → 104
    // floor(0.50*99)=49 → 149
    // floor(0.95*99)=94 → 194
    EXPECT_DOUBLE_EQ(s.p05, 104.0);
    EXPECT_DOUBLE_EQ(s.p50, 149.0);
    EXPECT_DOUBLE_EQ(s.p95, 194.0);
}

TEST(StatAccumulatorPercentile, SecondWindowAfterWrap)
{
    // Verify that after a flush following a ring-wrap, the next window
    // starts fresh and produces correct percentiles again
    StatAccumulator acc(50);
    for (int i = 0; i < 100; ++i) acc.update(static_cast<double>(i));
    acc.flush(); // discard first window

    for (int i = 0; i < 50; ++i) acc.update(static_cast<double>(i));
    auto s = acc.flush();

    EXPECT_EQ(s.count, 50u);
    // sorted: 0..49;  floor(0.95*49)=46 → 46
    EXPECT_DOUBLE_EQ(s.p95, 46.0);
}
