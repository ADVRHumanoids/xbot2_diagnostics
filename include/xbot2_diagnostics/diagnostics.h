#ifndef XBOT2_DIAGNOSTICS_DIAGNOSTICS_H
#define XBOT2_DIAGNOSTICS_DIAGNOSTICS_H

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

namespace XBot::diagnostics {

struct KeyValue {
    std::string key;
    double value = 0.0;
};

struct DiagnosticsStatus
{
    enum Level
    {
        OK = 0,
        WARNING = 1,
        ERROR = 2,
        UNKNOWN = 3
    };

    Level level = OK;
    std::string name;
    std::string msg;
    std::string hardware_id;
    std::vector<KeyValue> values;

    DiagnosticsStatus& reserve(std::size_t n_values, std::size_t str_len)
    {
        values.resize(n_values);
        name.reserve(str_len);
        msg.reserve(str_len);
        hardware_id.reserve(str_len);
        for (auto& kv : values) {
            kv.key.reserve(str_len);
        }

        return *this;
    }
};

} // namespace XBot::diagnostics

#endif // XBOT2_DIAGNOSTICS_DIAGNOSTICS_H
