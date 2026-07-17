#pragma once

#include <algorithm>
#include <charconv>
#include <chrono>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <diagnostic_msgs/msg/diagnostic_status.hpp>
#include <diagnostic_msgs/msg/key_value.hpp>
#include <rclcpp/rclcpp.hpp>

#include "diagnostics.h"
#include "stats_accumulator.h"

namespace XBot::diagnostics {

class Ros2DiagPublisher
{
public:
    using DiagnosticArrayMsg = diagnostic_msgs::msg::DiagnosticArray;
    using DiagnosticStatusMsg = diagnostic_msgs::msg::DiagnosticStatus;
    using KeyValueMsg = diagnostic_msgs::msg::KeyValue;

    Ros2DiagPublisher(rclcpp::Node& node,
                      std::string status_name,
                      std::string hw_id,
                      std::vector<std::string> value_keys = {},
                      std::string topic = "/diagnostics",
                      rclcpp::QoS qos = rclcpp::SystemDefaultsQoS(),
                      double throttle_publish_interval_sec = 0.0)
        : publisher_(node.create_publisher<DiagnosticArrayMsg>(std::move(topic), qos)),
          clock_(node.get_clock()),
          status_name_(std::move(status_name)),
          hw_id_(std::move(hw_id)),
          value_keys_(std::move(value_keys)),
          throttle_publish_interval_sec_(throttle_publish_interval_sec),
          last_publish_time_(std::chrono::steady_clock::now())
    {
        if (hw_id_.empty()) {
            hw_id_ = status_name_;
        }
    }

    bool should_publish() const
    {
        if (throttle_publish_interval_sec_.count() <= 0.0) {
            return true;
        }

        const auto now = std::chrono::steady_clock::now();
        return (now - last_publish_time_) >= throttle_publish_interval_sec_;
    }

    void publish(int level, const std::string& msg,
                 const std::vector<KeyValue>& values = {})
    {
        if (!should_publish()) {
            return;
        }

        DiagnosticsStatus status;
        status.level = static_cast<DiagnosticsStatus::Level>(level);
        status.name = status_name_;
        status.hardware_id = hw_id_;
        status.msg = msg;
        status.values = values;
        publish(status, std::chrono::steady_clock::now());
    }

    void publish(int level, const std::string& msg,
                 const std::vector<double>& values)
    {
        if (!should_publish()) {
            return;
        }

        DiagnosticsStatus status;
        status.level = static_cast<DiagnosticsStatus::Level>(level);
        status.name = status_name_;
        status.hardware_id = hw_id_;
        status.msg = msg;
        fill_keyed_values(values, status.values);
        publish(status, std::chrono::steady_clock::now());
    }

    void publish(const DiagnosticsStatus& status)
    {
        if (!should_publish()) {
            return;
        }

        publish(status, std::chrono::steady_clock::now());
    }

    void publish_stats(const std::string& metric_name,
                       StatAccumulator& acc,
                       int level = DiagnosticsStatus::OK,
                       const std::string& msg = "OK")
    {
        if (!should_publish()) {
            return;
        }

        auto time_ns = std::chrono::steady_clock::now().time_since_epoch().count();
        auto st = acc.flush(time_ns);

        publish(level, msg, {
            {metric_name + ".count", static_cast<double>(st.count)},
            {metric_name + ".mean",  st.mean},
            {metric_name + ".std_dev", st.std_dev},
            {metric_name + ".rms",   st.rms},
            {metric_name + ".min",   st.min},
            {metric_name + ".max",   st.max},
            {metric_name + ".p05",   st.p05},
            {metric_name + ".p50",   st.p50},
            {metric_name + ".p95",   st.p95},
            {metric_name + ".duration", st.duration}
        });
    }

private:
    void fill_keyed_values(const std::vector<double>& values, std::vector<KeyValue>& keyed_values) const
    {
        keyed_values.clear();
        keyed_values.reserve(std::min(values.size(), value_keys_.size()));
        for (std::size_t i = 0; i < values.size() && i < value_keys_.size(); ++i) {
            keyed_values.push_back({value_keys_[i], values[i]});
        }
    }

    void publish(const DiagnosticsStatus& status, std::chrono::steady_clock::time_point now)
    {
        DiagnosticArrayMsg array_msg;
        array_msg.header.stamp = clock_->now();
        array_msg.status.push_back(to_ros_status(status));
        publisher_->publish(array_msg);
        last_publish_time_ = now;
    }

    static DiagnosticStatusMsg to_ros_status(const DiagnosticsStatus& status)
    {
        DiagnosticStatusMsg ros_status;
        ros_status.level = static_cast<unsigned char>(status.level);
        ros_status.name = status.name;
        ros_status.message = status.msg;
        ros_status.hardware_id = status.hardware_id;
        ros_status.values.reserve(status.values.size());
        for (const auto& kv : status.values) {
            KeyValueMsg ros_kv;
            ros_kv.key = kv.key;
            ros_kv.value = double_to_string(kv.value);
            ros_status.values.push_back(std::move(ros_kv));
        }
        return ros_status;
    }

    static std::string double_to_string(double value)
    {
        char tmp[32];
        auto [ptr, ec] = std::to_chars(tmp, tmp + sizeof(tmp), value);
        if (ec == std::errc()) {
            return std::string(tmp, ptr);
        }

        std::ostringstream os;
        os << value;
        return os.str();
    }

    rclcpp::Publisher<DiagnosticArrayMsg>::SharedPtr publisher_;
    rclcpp::Clock::SharedPtr clock_;
    std::string status_name_;
    std::string hw_id_;
    std::vector<std::string> value_keys_;
    std::chrono::duration<double> throttle_publish_interval_sec_;
    std::chrono::steady_clock::time_point last_publish_time_;
};

class Ros2StatsPublisher
{
public:
    struct Thresholds {
        double peak_min = -std::numeric_limits<double>::infinity();
        double peak_max = std::numeric_limits<double>::infinity();
        double avg_min = -std::numeric_limits<double>::infinity();
        double avg_max = std::numeric_limits<double>::infinity();
        double rms_min = 0.0;
        double rms_max = std::numeric_limits<double>::infinity();
        int count_min = std::numeric_limits<int>::min();
        int count_max = std::numeric_limits<int>::max();
    };

    Ros2StatsPublisher(rclcpp::Node& node,
                       std::string status_name,
                       std::string hw_id,
                       std::string stats_name,
                       double publish_interval_sec,
                       std::size_t perc_capacity = 0,
                       std::string topic = "/diagnostics",
                       rclcpp::QoS qos = rclcpp::SystemDefaultsQoS())
        : acc_(perc_capacity),
          diag_pub_(std::make_unique<Ros2DiagPublisher>(
              node,
              std::move(status_name),
              std::move(hw_id),
              prefixed_value_keys(stats_name, acc_.value_keys),
              std::move(topic),
              qos,
              publish_interval_sec)),
          stats_name_(std::move(stats_name))
    {
        values_.reserve(acc_.value_keys.size());
        msg_.reserve(128);
    }

    void update_and_publish(double value)
    {
        acc_.update(value);

        if (diag_pub_->should_publish()) {
            auto time_ns = std::chrono::steady_clock::now().time_since_epoch().count();
            auto stats = acc_.flush(time_ns);
            stats.to_std_vector(values_);

            auto level = msg_from_stats(stats, msg_);
            diag_pub_->publish(level, msg_, values_);
        }
    }

    Thresholds& warning_thresholds() { return warning_thresholds_; }
    Thresholds& error_thresholds() { return error_thresholds_; }

private:
    static std::vector<std::string> prefixed_value_keys(
        const std::string& stats_name,
        const std::vector<std::string>& value_keys)
    {
        std::vector<std::string> out;
        out.reserve(value_keys.size());
        for (const auto& key : value_keys) {
            out.push_back(stats_name + "." + key);
        }
        return out;
    }

    template <typename T>
    static std::string value_to_string(T value)
    {
        std::ostringstream os;
        os << value;
        return os.str();
    }

    template <typename T>
    void format_msg(std::string& msg,
                    const std::string& stat_name,
                    const char* relation,
                    T value,
                    T threshold,
                    const StatAccumulator::Stats& stats,
                    bool error) const
    {
        msg.clear();
        msg += stats_name_;
        msg += " ";
        msg += stat_name;
        if (error) {
            msg += " too";
        }
        msg += " ";
        msg += relation;
        msg += ": ";
        msg += value_to_string(value);
        msg += relation[0] == 'h' ? " >= " : " <= ";
        msg += value_to_string(threshold);
        msg += " (n = ";
        msg += value_to_string(stats.count);
        msg += ")";
    }

    DiagnosticsStatus::Level msg_from_stats(const StatAccumulator::Stats& stats, std::string& msg)
    {
        auto check_thresholds =
            [&](const Thresholds& thresholds, bool error) -> DiagnosticsStatus::Level
        {
            auto high = [&](const char* stat_name, double value, double threshold) -> bool
            {
                if (value <= threshold) {
                    return false;
                }
                format_msg(msg, stat_name, "high", value, threshold, stats, error);
                return true;
            };
            auto low = [&](const char* stat_name, double value, double threshold) -> bool
            {
                if (value >= threshold) {
                    return false;
                }
                format_msg(msg, stat_name, "low", value, threshold, stats, error);
                return true;
            };

            if (high("peak", stats.max, thresholds.peak_max) ||
                low("peak", stats.min, thresholds.peak_min) ||
                high("avg", stats.mean, thresholds.avg_max) ||
                low("avg", stats.mean, thresholds.avg_min) ||
                high("rms", stats.rms, thresholds.rms_max) ||
                low("rms", stats.rms, thresholds.rms_min) ||
                high("count", static_cast<double>(stats.count), static_cast<double>(thresholds.count_max)) ||
                low("count", static_cast<double>(stats.count), static_cast<double>(thresholds.count_min))) {
                return error ? DiagnosticsStatus::ERROR : DiagnosticsStatus::WARNING;
            }

            return DiagnosticsStatus::OK;
        };

        auto level = check_thresholds(error_thresholds_, true);
        if (level != DiagnosticsStatus::OK) {
            return level;
        }

        level = check_thresholds(warning_thresholds_, false);
        if (level != DiagnosticsStatus::OK) {
            return level;
        }

        msg.clear();
        msg += stats_name_;
        msg += " OK (n = ";
        msg += value_to_string(stats.count);
        msg += ")";
        return DiagnosticsStatus::OK;
    }

    StatAccumulator acc_;
    std::unique_ptr<Ros2DiagPublisher> diag_pub_;
    std::vector<double> values_;
    std::string msg_;
    std::string stats_name_;
    Thresholds warning_thresholds_;
    Thresholds error_thresholds_;
};

} // namespace XBot::diagnostics
