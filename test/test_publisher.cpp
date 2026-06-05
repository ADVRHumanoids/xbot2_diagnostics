#include <gtest/gtest.h>
#include <cmath>
#include <memory>
#include <numeric>
#include <nlohmann/json.hpp>

#include "xbot2_diagnostics/publisher.h"

using XBot::diagnostics::DiagPublisher;

// ── helpers ────────────────────────────────────────────────────────────────

static constexpr double kEps = 1e-9;

// ── no-capacity (legacy) behaviour ─────────────────────────────────────────

TEST(DiagPublisher, DefaultConstruction)
{
    auto ctx = std::make_shared<zmq::context_t>(1);
    const std::string endpoint = "inproc://test_publisher_default_construction";

    // define zmq subscriber to receive the message published by DiagPublisher
    zmq::socket_t sub(*ctx, zmq::socket_type::pull);
    sub.bind(endpoint);

    DiagPublisher pub("test_node", "test_hw", endpoint, ctx);

    // publish a message
    pub.publish(0, "Test message", {{"metric1", 42.0}, {"metric2", 3.14}});

    // receive the message
    zmq::message_t msg;
    auto ret = sub.recv(msg, zmq::recv_flags::none);
    ASSERT_TRUE(ret.has_value());
    ASSERT_GT(msg.size(), 0u);
    std::string msg_str(static_cast<char*>(msg.data()), msg.size());

    // parse the message as JSON
    auto j = nlohmann::json::parse(msg_str);
    EXPECT_EQ(j["v"], 1);
    EXPECT_EQ(j["node"], "test_node");
    EXPECT_EQ(j["hw_id"], "test_hw");
    EXPECT_EQ(j["level"], 0);
    EXPECT_EQ(j["msg"], "Test message");
    EXPECT_EQ(j["values"].size(), 2);
    EXPECT_EQ(j["values"][0][0], "metric1");
    EXPECT_DOUBLE_EQ(j["values"][0][1], 42.0);
    EXPECT_EQ(j["values"][1][0], "metric2");
    EXPECT_DOUBLE_EQ(j["values"][1][1], 3.14);

}
