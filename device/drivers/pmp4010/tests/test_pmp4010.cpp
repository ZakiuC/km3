// 隔离契约测试：只使用 MockCanDevice，不打开真实总线。
// KM3_ALLOW_UNVERIFIED_OD 仅允许测试编译，运行时 OD 守卫仍必须关闭。
#include <array>
#include <cstdint>
#include <limits>
#include <string>
#include <vector>

#include "../driver/pmp4010_driver.hpp"
#include "km3/hal/mock_devices.hpp"
#include "test_harness.hpp"

using km3::CanBackend;
using km3::ErrorCode;
using km3::MockCanDevice;
using km3::pmp4010::Direction;
using km3::pmp4010::DriverConfig;
using km3::pmp4010::kOdVerified;
using km3::pmp4010::NmtCommand;
using km3::pmp4010::Pmp4010Driver;
namespace od = km3::pmp4010::od;

namespace {

DriverConfig MakeConfig() {
    DriverConfig config;
    config.logical_bus = "can-2";
    // 仅为测试分配的合法地址，不是硬件节点号的默认值。
    for (std::size_t i = 0; i < config.nodes.size(); ++i) {
        config.nodes[i].node_id = static_cast<std::uint8_t>(i + 1);
    }
    return config;
}

struct Fixture {
    MockCanDevice device{"can-2", CanBackend::SocketCan};
    Pmp4010Driver driver{device, MakeConfig()};
};

// 用同一组 API 验证已打开 / 未打开 / 无效节点 / HAL 故障等场景。
// SDO 索引也故意无效：守卫必须在解析或收发任何请求前拒绝调用。
void CheckAllProtocolCallsAreGuarded(Pmp4010Driver& driver, std::uint8_t node) {
    const auto before = driver.Stats().rejected_by_od_guard;
    KM3_CHECK(driver.Enable(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.Disable(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.MoveTo(node, std::numeric_limits<std::int32_t>::min()).code() ==
              Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.SetVelocity(node, std::numeric_limits<std::int32_t>::max(), Direction::Raise)
                  .code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.SetVelocity(node, -1, Direction::Lower).code() ==
              Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.Stop(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.Home(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(driver.SendNmt(NmtCommand::Start, node).code() == Pmp4010Driver::kOdGuardError);
    const auto probe = driver.ProbeNode(node);
    KM3_CHECK(!probe.ok());
    KM3_CHECK(probe.code() == Pmp4010Driver::kOdGuardError);

    const std::vector<std::uint8_t> expected{0xDE, 0xAD, 0xBE};
    auto output = expected;
    KM3_CHECK(driver.SdoRead(node, od::kIndexUnset, 0, output).code() ==
              Pmp4010Driver::kOdGuardError);
    KM3_CHECK(output == expected);
    KM3_CHECK(driver.SdoWrite(node, od::kIndexUnset, 0, {}).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK_EQ(driver.Stats().rejected_by_od_guard - before, std::uint64_t{11});
}

void CheckNoIo(const Fixture& fixture) {
    KM3_CHECK(fixture.device.SentFrames().empty());
    KM3_CHECK_EQ(fixture.device.Stats().tx_frames, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.device.Stats().rx_frames, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.device.Stats().tx_errors, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.driver.Stats().tx_frames, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.driver.Stats().rx_frames, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.driver.Stats().sdo_timeouts, std::uint64_t{0});
    KM3_CHECK_EQ(fixture.driver.Stats().rejected_by_validation, std::uint64_t{0});
}

}  // namespace

KM3_TEST(partial_od_keeps_the_runtime_guard_closed) {
    KM3_CHECK(!kOdVerified);
    KM3_CHECK(Pmp4010Driver::UnverifiedOdCount() > 0);
    KM3_CHECK(Pmp4010Driver::UnverifiedOdCount() < Pmp4010Driver::OdEntryCount());
    KM3_CHECK_EQ(kOdVerified, od::kOdVerified);
    KM3_CHECK(Pmp4010Driver::kOdGuardError == ErrorCode::EDeviceError);
}

KM3_TEST(od_metadata_and_lookup_preserve_verification_status) {
    std::size_t unverified_count = 0;
    std::size_t verified_indices = 0;
    std::size_t verified_metadata = 0;
    for (std::size_t i = 0; i < Pmp4010Driver::OdEntryCount(); ++i) {
        const auto& entry = od::kEntries[i];
        KM3_REQUIRE(entry.key != nullptr);
        KM3_REQUIRE(entry.cite != nullptr);
        KM3_CHECK(entry.key[0] != '\0');
        KM3_CHECK_EQ(entry.verified, entry.status == od::OdStatus::Verified);
        if (entry.verified) {
            KM3_CHECK(entry.cite[0] != '\0');
            // 位定义、标度、协议说明等已核对事实没有可寻址的 index。
            // 核对状态不等于存在对象索引，lookup 必须保留这个区别。
            KM3_CHECK_EQ(od::pmp4010_index(i), entry.index);
            if (entry.index == od::kIndexUnset) {
                ++verified_metadata;
            } else {
                ++verified_indices;
            }
        } else {
            KM3_CHECK_EQ(entry.index, od::kIndexUnset);
            KM3_CHECK_EQ(od::pmp4010_index(i), od::kIndexUnset);
            if (entry.status == od::OdStatus::Unverified) {
                KM3_CHECK(entry.cite[0] == '\0');
            } else {
                KM3_CHECK(entry.status == od::OdStatus::Assumed);
            }
            ++unverified_count;
        }
        for (std::size_t j = i + 1; j < Pmp4010Driver::OdEntryCount(); ++j) {
            KM3_CHECK(std::string(entry.key) != od::kEntries[j].key);
        }
    }
    KM3_CHECK(verified_indices > 0);
    KM3_CHECK(verified_metadata > 0);
    KM3_CHECK_EQ(unverified_count, Pmp4010Driver::UnverifiedOdCount());
    KM3_CHECK_EQ(unverified_count + verified_indices + verified_metadata,
                 Pmp4010Driver::OdEntryCount());
    KM3_CHECK_EQ(od::pmp4010_index(Pmp4010Driver::OdEntryCount()), od::kIndexUnset);
    KM3_CHECK_EQ(od::pmp4010_index(std::numeric_limits<std::size_t>::max()), od::kIndexUnset);
}

KM3_TEST(open_requires_an_open_backend) {
    Fixture fixture;
    KM3_CHECK(fixture.driver.Open().code() == ErrorCode::EDeviceOffline);
    KM3_CHECK(!fixture.driver.IsOpen());
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_CHECK(fixture.driver.Open().ok());
    KM3_CHECK(fixture.driver.IsOpen());
    CheckNoIo(fixture);
}

KM3_TEST(open_rejects_the_usbcan_backend) {
    MockCanDevice device("usb-can-1", CanBackend::UsbCan);
    KM3_REQUIRE(device.Open().ok());
    auto config = MakeConfig();
    config.logical_bus = "usb-can-1";
    Pmp4010Driver driver(device, config);
    KM3_CHECK(driver.Open().code() == ErrorCode::EInvalidRequest);
    KM3_CHECK(!driver.IsOpen());
    KM3_CHECK(device.SentFrames().empty());
}

KM3_TEST(open_requires_matching_nonempty_logical_names) {
    MockCanDevice device("can-2", CanBackend::SocketCan);
    KM3_REQUIRE(device.Open().ok());
    for (const std::string name : {"", "can-1"}) {
        auto config = MakeConfig();
        config.logical_bus = name;
        Pmp4010Driver driver(device, config);
        KM3_CHECK(driver.Open().code() == ErrorCode::EInvalidConfig);
        KM3_CHECK(!driver.IsOpen());
    }
    KM3_CHECK(device.SentFrames().empty());
}

KM3_TEST(open_rejects_invalid_and_duplicate_node_addresses) {
    MockCanDevice device("can-2", CanBackend::SocketCan);
    KM3_REQUIRE(device.Open().ok());
    for (const auto address : std::array<std::uint8_t, 3>{0, 128, 255}) {
        for (std::size_t i = 0; i < MakeConfig().nodes.size(); ++i) {
            auto config = MakeConfig();
            config.nodes[i].node_id = address;
            Pmp4010Driver driver(device, config);
            KM3_CHECK(driver.Open().code() == ErrorCode::EInvalidConfig);
            KM3_CHECK(!driver.IsOpen());
        }
    }
    for (std::size_t i = 1; i < MakeConfig().nodes.size(); ++i) {
        auto config = MakeConfig();
        config.nodes[i].node_id = config.nodes[0].node_id;
        Pmp4010Driver driver(device, config);
        KM3_CHECK(driver.Open().code() == ErrorCode::EInvalidConfig);
    }
    KM3_CHECK(device.SentFrames().empty());
}

KM3_TEST(open_accepts_the_highest_node_address) {
    MockCanDevice device("can-2", CanBackend::SocketCan);
    KM3_REQUIRE(device.Open().ok());
    auto config = MakeConfig();
    config.nodes.back().node_id = 127;
    Pmp4010Driver driver(device, config);
    KM3_CHECK(driver.Open().ok());
    KM3_CHECK(driver.IsOpen());
    KM3_CHECK(device.SentFrames().empty());
}

KM3_TEST(open_rechecks_a_backend_closed_by_its_owner) {
    Fixture fixture;
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_REQUIRE(fixture.driver.Open().ok());
    KM3_CHECK(fixture.driver.Open().ok());
    KM3_REQUIRE(fixture.device.Close().ok());
    KM3_CHECK(!fixture.driver.IsOpen());
    KM3_CHECK(fixture.driver.Open().code() == ErrorCode::EDeviceOffline);
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_CHECK(!fixture.driver.IsOpen());
    KM3_CHECK(fixture.driver.Open().ok());
    KM3_CHECK(fixture.driver.IsOpen());
    CheckNoIo(fixture);
}

KM3_TEST(close_and_destruction_preserve_the_callers_backend) {
    MockCanDevice device("can-2", CanBackend::SocketCan);
    KM3_REQUIRE(device.Open().ok());
    {
        Pmp4010Driver driver(device, MakeConfig());
        KM3_REQUIRE(driver.Open().ok());
        KM3_CHECK(driver.Close().ok());
        KM3_CHECK(driver.Close().ok());
        KM3_CHECK(!driver.IsOpen());
        KM3_CHECK(device.IsOpen());
        KM3_CHECK(driver.Open().ok());
    }
    KM3_CHECK(device.IsOpen());
    KM3_CHECK(device.SentFrames().empty());
}

KM3_TEST(all_protocol_calls_are_guarded_on_an_open_driver) {
    Fixture fixture;
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_REQUIRE(fixture.driver.Open().ok());
    CheckAllProtocolCallsAreGuarded(fixture.driver, 1);
    CheckNoIo(fixture);
}

KM3_TEST(od_guard_precedes_lifecycle_and_node_validation) {
    Fixture fixture;
    for (const auto node : std::array<std::uint8_t, 4>{0, 99, 128, 255}) {
        CheckAllProtocolCallsAreGuarded(fixture.driver, node);
    }
    CheckNoIo(fixture);
}

KM3_TEST(guarded_calls_do_not_consume_queued_frames_or_trigger_backend_errors) {
    Fixture fixture;
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_REQUIRE(fixture.driver.Open().ok());
    km3::CanFrame pending;
    fixture.device.InjectRx(pending);
    fixture.device.Faults().bus_off = true;
    fixture.device.Faults().timeout = true;
    CheckAllProtocolCallsAreGuarded(fixture.driver, 1);
    KM3_CHECK_EQ(fixture.device.PendingRxCount(), std::size_t{1});
    CheckNoIo(fixture);
}

KM3_TEST(all_nmt_commands_including_broadcast_are_guarded) {
    Fixture fixture;
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_REQUIRE(fixture.driver.Open().ok());
    for (const auto command : {NmtCommand::Start, NmtCommand::Stop, NmtCommand::PreOperational,
                               NmtCommand::ResetNode, NmtCommand::ResetCommunication}) {
        KM3_CHECK(fixture.driver.SendNmt(command, 0).code() == Pmp4010Driver::kOdGuardError);
        KM3_CHECK(fixture.driver.SendNmt(command, 1).code() == Pmp4010Driver::kOdGuardError);
    }
    KM3_CHECK_EQ(fixture.driver.Stats().rejected_by_od_guard, std::uint64_t{10});
    CheckNoIo(fixture);
}

KM3_TEST(configuration_is_a_snapshot_without_invented_runtime_parameters) {
    MockCanDevice device("can-2", CanBackend::SocketCan);
    auto config = MakeConfig();
    config.sdo_timeout_ms = 250;
    config.heartbeat_period_ms = 1000;
    Pmp4010Driver driver(device, config);
    config.logical_bus = "can-1";
    config.nodes[0].node_id = 99;
    KM3_CHECK_EQ(driver.LogicalBus(), std::string("can-2"));
    KM3_CHECK_EQ(driver.Config().nodes[0].node_id, std::uint8_t{1});
    KM3_CHECK_EQ(driver.Config().sdo_timeout_ms, std::uint32_t{250});
    KM3_CHECK_EQ(driver.Config().heartbeat_period_ms, std::uint32_t{1000});
    KM3_CHECK_EQ(driver.Config().nodes[0].counts_per_unit, 0.0);
    KM3_CHECK(&driver.Device() == &device);
}

KM3_TEST(new_typed_and_bounded_apis_keep_the_global_zero_frame_guard) {
    Fixture fixture;
    KM3_REQUIRE(fixture.device.Open().ok());
    KM3_REQUIRE(fixture.driver.Open().ok());
    auto& driver = fixture.driver;
    for (const auto node : std::array<std::uint8_t, 3>{1, 0, 255}) {
        KM3_CHECK_EQ(driver.ReadBytes(node, 0x6041, 0).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.ReadObject(node, 0x6041, 0).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.ReadString(node, 0x1008, 0).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.WriteObject(node, 0x6040, 0, 15).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.Shutdown(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.SwitchOn(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.EnableOperation(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.QuickStop(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.FaultReset(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.ReadStatusword(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.SetOperationMode(node, 6).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.GetOperationMode(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.SetTargetPosition(node, -1).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.SetTargetVelocity(node, -1).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.GetActualPosition(node).code(), Pmp4010Driver::kOdGuardError);
        KM3_CHECK_EQ(driver.GetActualVelocity(node).code(), Pmp4010Driver::kOdGuardError);
    }
    CheckNoIo(fixture);
}

KM3_TEST_MAIN()
