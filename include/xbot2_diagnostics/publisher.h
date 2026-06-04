// diag/diag_publisher.hpp
#pragma once
#include <zmq.hpp>
#include "stats_accumulator.h"
#include <charconv>
#include <string>
#include <vector>
#include <functional>

namespace XBot::diagnostics {

struct KeyValue { std::string key; double value; };

class DiagPublisher {
public:
    DiagPublisher(zmq::context_t& ctx,
                  std::string node_name,
                  std::string hw_id,
                  std::string endpoint = "")
        : socket_(ctx, zmq::socket_type::push),
          node_(std::move(node_name)), hw_id_(std::move(hw_id))
    {
        if (endpoint.empty())
        {
            std::getenv("XBOT_DIAG_ENDPOINT") ?
                endpoint = std::getenv("XBOT_DIAG_ENDPOINT") :
                endpoint = "tcp://localhost:9268";
        }

        socket_.connect(endpoint);
    }

    // Call from diagnostics timer (1–10 Hz)
    void publish(int level, const std::string& msg,
                 const std::vector<KeyValue>& values = {})
    {
        _buf.clear();
        _buf += R"({"v":1,"node":")"; _append_escaped(_buf, node_);
        _buf += R"(","hw_id":")";     _append_escaped(_buf, hw_id_);
        _buf += R"(","stamp":)";      _append_double(_buf, timestamp_now());
        _buf += R"(,"level":)";       _buf += std::to_string(level);
        _buf += R"(,"msg":")";        _append_escaped(_buf, msg);
        _buf += R"(","values":[)";
        for (std::size_t i = 0; i < values.size(); ++i) {
            if (i) _buf += ',';
            _buf += "[\"";
            _append_escaped(_buf, values[i].key);
            _buf += "\",";
            _append_double(_buf, values[i].value);
            _buf += ']';
        }
        _buf += "]}";
        socket_.send(zmq::buffer(_buf), zmq::send_flags::dontwait);
    }

    // Convenience: flush a StatAccumulator and publish its stats
    void publish_stats(const std::string& metric_name,
                       StatAccumulator& acc,
                       int level = 0, const std::string& msg = "OK")
    {
        auto st = acc.flush();
        publish(level, msg, {
            {metric_name + ".mean",  st.mean},
            {metric_name + ".std",   st.std_dev},
            {metric_name + ".min",   st.min},
            {metric_name + ".max",   st.max},
            {metric_name + ".p05",   st.p05},
            {metric_name + ".p50",   st.p50},
            {metric_name + ".p95",   st.p95},
            {metric_name + ".count", (double)st.count},
        });
    }

private:
    double timestamp_now() {
        using namespace std::chrono;
        auto now = system_clock::now();
        auto epoch = now.time_since_epoch();
        return duration_cast<duration<double>>(epoch).count();
    }

    static void _append_double(std::string& out, double v) {
        char tmp[32];
        auto [ptr, ec] = std::to_chars(tmp, tmp + sizeof(tmp), v);
        out.append(tmp, ptr);
    }

    static void _append_escaped(std::string& out, const std::string& s) {
        for (unsigned char c : s) {
            switch (c) {
                case '"':  out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\n': out += "\\n";  break;
                case '\r': out += "\\r";  break;
                case '\t': out += "\\t";  break;
                default:
                    if (c < 0x20) {          // remaining control chars
                        char buf[8];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                        out += buf;
                    } else {
                        out += static_cast<char>(c);
                    }
            }
        }
    }

    zmq::socket_t socket_;
    std::string node_, hw_id_;
    std::string _buf;  // reused scratch buffer — zero heap alloc after warm-up
};

} // namespace XBot::diagnostics