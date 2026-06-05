#include <atomic>
#include <charconv>
#include <chrono>
#include <cstdio>
#include <memory>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include <zmq.hpp>
#include <nlohmann/json.hpp>

#include "xbot2_diagnostics/publisher.h"

using namespace XBot::diagnostics;
using Clock = std::chrono::steady_clock;
using ns    = std::chrono::nanoseconds;

static double elapsed_ns(Clock::time_point t0, Clock::time_point t1)
{
    return static_cast<double>(std::chrono::duration_cast<ns>(t1 - t0).count());
}

// ── drain thread: pulls messages so the HWM never blocks the sender ────────
static std::atomic<bool> g_stop{false};

static void drain_thread(zmq::context_t& ctx, const std::string& endpoint)
{
    zmq::socket_t pull(ctx, zmq::socket_type::pull);
    pull.bind(endpoint);
    zmq::message_t msg;
    while (!g_stop.load(std::memory_order_relaxed))
    {
        // 5 ms timeout so the thread wakes up and checks g_stop
        if (pull.recv(msg, zmq::recv_flags::dontwait))
            continue;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

int main()
{
    constexpr std::size_t ITERS   = 50'000;
    constexpr std::size_t WARMUP  = 1'000;
    const std::string     EP      = "tcp://127.0.0.1:9370";

    auto ctx = std::make_shared<zmq::context_t>(1);

    // start drain thread before connecting the sender
    std::thread drainer(drain_thread, std::ref(*ctx), EP);
    std::this_thread::sleep_for(std::chrono::milliseconds(50)); // let bind settle

    DiagPublisher pub("profiler_node", "hw0", EP, ctx);

    // pre-build a representative KeyValue list (8 metrics, same as publish_stats)
    std::vector<KeyValue> values = {
        {"loop_dt.mean",  1.23},
        {"loop_dt.std",   0.04},
        {"loop_dt.min",   0.95},
        {"loop_dt.max",   1.80},
        {"loop_dt.p05",   1.00},
        {"loop_dt.p50",   1.22},
        {"loop_dt.p95",   1.55},
        {"loop_dt.count", 10000.0},
    };

    // ── warm-up ────────────────────────────────────────────────────────────
    for (std::size_t i = 0; i < WARMUP; ++i)
        pub.publish(0, "ok", values);

    // ── time total publish() ───────────────────────────────────────────────
    auto t0 = Clock::now();
    for (std::size_t i = 0; i < ITERS; ++i)
        pub.publish(0, "ok", values);
    auto t1 = Clock::now();

    double total_publish_ns = elapsed_ns(t0, t1);

    // ── time JSON build + dump only (no socket) — nlohmann baseline ───────
    double total_nlohmann_ns = 0.0;
    for (std::size_t i = 0; i < ITERS; ++i)
    {
        auto tj0 = Clock::now();
        nlohmann::json j;
        j["v"]     = 1;
        j["node"]  = "profiler_node";
        j["hw_id"] = "hw0";
        j["stamp"] = 1.0;
        j["level"] = 0;
        j["msg"]   = "ok";
        for (auto& kv : values)
            j["values"].push_back({kv.key, kv.value});
        auto s = j.dump();
        (void)s;
        auto tj1 = Clock::now();
        total_nlohmann_ns += elapsed_ns(tj0, tj1);
    }

    // ── time hand-rolled JSON only (same logic as publish(), minus send) ───
    std::string scratch;
    auto t2 = Clock::now();
    for (std::size_t i = 0; i < ITERS; ++i)
    {
        scratch.clear();
        scratch += R"({"v":1,"node":"profiler_node","hw_id":"hw0","stamp":1.0,"level":0,"msg":"ok","values":[)";
        for (std::size_t k = 0; k < values.size(); ++k) {
            if (k) scratch += ',';
            scratch += "[\""; scratch += values[k].key; scratch += "\",";
            char tmp[32];
            auto [ptr, ec] = std::to_chars(tmp, tmp + sizeof(tmp), values[k].value);
            scratch.append(tmp, ptr);
            scratch += ']';
        }
        scratch += "]}";
    }
    auto t3 = Clock::now();
    double total_handrolled_ns = elapsed_ns(t2, t3);

    double mean_publish_us    = elapsed_ns(t0, t1)      / static_cast<double>(ITERS) / 1e3;
    double mean_nlohmann_us   = total_nlohmann_ns        / static_cast<double>(ITERS) / 1e3;
    double mean_handrolled_us = total_handrolled_ns      / static_cast<double>(ITERS) / 1e3;
    double mean_send_us       = mean_publish_us - mean_handrolled_us;

    std::printf("\nprofile_publish  (n=%zu, 8-field message)\n", ITERS);
    std::printf("  %-36s  %8.3f µs\n", "nlohmann JSON build+dump (baseline)", mean_nlohmann_us);
    std::printf("  %-36s  %8.3f µs\n", "hand-rolled JSON build+dump",         mean_handrolled_us);
    std::printf("  %-36s  %8.3f µs  (estimated)\n", "zmq send (DONTWAIT)",    mean_send_us);
    std::printf("  %-36s  %8.3f µs\n", "publish() total",                     mean_publish_us);

    g_stop.store(true);
    drainer.join();
    return 0;
}
