// HAL 接口与模拟设备的验证。
//
// 重点不是"接口能调用"，而是**能力边界被强制执行**：
//   主板 CAN 是 CAN 2.0B，不支持 CAN FD；USB-CAN 才支持。
//   把 FD 帧发到经典后端必须被拒绝，而不是静默丢帧。
// 这一类错误在现场表现为"电机不动但不报错"，最难排查。
#include <string>
#include <vector>

#include "km3/clock.hpp"
#include "km3/hal/can_device.hpp"
#include "km3/hal/mock_devices.hpp"
#include "km3/hal/rs485_device.hpp"
#include "test_harness.hpp"

using km3::CanBackend;
using km3::CanCapabilities;
using km3::CanFrame;
using km3::ErrorCode;
using km3::MockCanDevice;
using km3::MockRs485Device;
using km3::ModbusFunction;
using km3::Rs485Backend;
using km3::SerialConfig;

namespace {

CanFrame MakeFrame(std::uint32_t id, std::initializer_list<std::uint8_t> bytes) {
    CanFrame f;
    f.id = id;
    f.dlc = static_cast<std::uint8_t>(bytes.size());
    std::size_t i = 0;
    for (auto b : bytes) {
        f.data[i++] = b;
    }
    return f;
}

}  // namespace

// ======================= CAN：能力边界 =======================

KM3_TEST(socketcan_backend_does_not_support_can_fd) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    const CanCapabilities caps = dev.Capabilities();
    // 这是本板最关键的硬件事实：主板 CAN 经 SPI + SIT2515，仅 CAN 2.0B。
    KM3_CHECK(!caps.supports_fd);
    KM3_CHECK_EQ(caps.max_bitrate, std::uint32_t{1000000});
}

KM3_TEST(usbcan_backend_supports_can_fd) {
    MockCanDevice dev("usb-can-1", CanBackend::UsbCan);
    KM3_CHECK(dev.Open().ok());
    // P65MAX-S 走 USB 转 CAN，必须支持 FD。
    KM3_CHECK(dev.Capabilities().supports_fd);
}

KM3_TEST(fd_frame_is_rejected_on_classic_backend) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    CanFrame fd = MakeFrame(0x123, {0xDE, 0xAD});
    fd.fd = true;
    fd.dlc = 16;                     // FD 长度

    const km3::Status s = dev.Send(fd);
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == ErrorCode::EInvalidRequest);
    // 关键：被拒绝时不得同时记录为已发送
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{0});
    KM3_CHECK_EQ(dev.Stats().tx_errors, std::uint64_t{1});
}

KM3_TEST(classic_frame_over_8_bytes_is_rejected) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    CanFrame f = MakeFrame(0x100, {1, 2, 3, 4, 5, 6, 7, 8});
    f.dlc = 9;                       // 经典帧上限 8
    KM3_CHECK(!dev.Send(f).ok());
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{0});
}

KM3_TEST(payload_length_does_not_silently_clamp) {
    // 回归断言。早期 PayloadLength() 会把越界的 dlc 钳到上限，
    // 而发送校验又基于它比较，导致**越界帧被静默放行**。
    // 现在取值与合法性分离，这里把该契约钉死。
    CanFrame classic;
    classic.fd = false;
    classic.dlc = 9;
    KM3_CHECK_EQ(classic.MaxPayloadBytes(), std::uint8_t{8});
    KM3_CHECK(!classic.IsDlcValid());          // 越界必须被识别
    KM3_CHECK_EQ(classic.PayloadLength(), std::uint8_t{9});  // 原样返回，不掩盖

    CanFrame ok_classic;
    ok_classic.fd = false;
    ok_classic.dlc = 8;
    KM3_CHECK(ok_classic.IsDlcValid());

    CanFrame fd_frame;
    fd_frame.fd = true;
    fd_frame.dlc = 64;
    KM3_CHECK_EQ(fd_frame.MaxPayloadBytes(), std::uint8_t{64});
    KM3_CHECK(fd_frame.IsDlcValid());

    CanFrame bad_fd;
    bad_fd.fd = true;
    bad_fd.dlc = 65;
    KM3_CHECK(!bad_fd.IsDlcValid());
}

KM3_TEST(fd_frame_is_accepted_on_usbcan_backend) {
    MockCanDevice dev("usb-can-1", CanBackend::UsbCan);
    KM3_CHECK(dev.Open().ok());

    CanFrame f = MakeFrame(0x200, {1, 2, 3, 4});
    f.fd = true;
    f.dlc = 32;                      // FD 允许 64 以内
    KM3_CHECK(dev.Send(f).ok());
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{1});
    KM3_CHECK_EQ(dev.Stats().tx_frames, std::uint64_t{1});
}

// ======================= CAN：收发与故障 =======================

KM3_TEST(can_send_is_recorded_and_receive_is_fifo) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    KM3_CHECK(dev.Send(MakeFrame(0x101, {0x01})).ok());
    KM3_CHECK(dev.Send(MakeFrame(0x102, {0x02, 0x03})).ok());
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{2});
    KM3_CHECK_EQ(dev.SentFrames()[1].id, std::uint32_t{0x102});

    dev.InjectRx(MakeFrame(0x201, {0xAA}));
    dev.InjectRx(MakeFrame(0x202, {0xBB}));
    KM3_CHECK_EQ(dev.PendingRxCount(), std::size_t{2});

    auto r1 = dev.Receive(0);
    KM3_CHECK(r1.ok());
    KM3_CHECK_EQ(r1.value().id, std::uint32_t{0x201});
    auto r2 = dev.Receive(0);
    KM3_CHECK(r2.ok());
    KM3_CHECK_EQ(r2.value().id, std::uint32_t{0x202});
}

KM3_TEST(can_empty_receive_is_timeout_not_error) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());
    auto r = dev.Receive(0);
    KM3_CHECK(!r.ok());
    // 总线空闲是正常状态；若把它当错误，上层会疯狂重连。
    KM3_CHECK(r.code() == ErrorCode::ECommandTimeout);
}

KM3_TEST(can_bus_off_blocks_io_and_recovers) {
    MockCanDevice dev("can-2", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    dev.Faults().bus_off = true;
    KM3_CHECK(!dev.Send(MakeFrame(0x300, {0x01})).ok());
    KM3_CHECK(dev.StatusOf().state == km3::DeviceStatusEnum::BusOff);
    KM3_CHECK(!dev.Receive(0).ok());

    KM3_CHECK(dev.RecoverFromBusOff().ok());
    KM3_CHECK_EQ(dev.Stats().bus_off_count, std::uint64_t{1});
    KM3_CHECK(dev.StatusOf().state == km3::DeviceStatusEnum::Open);
    KM3_CHECK(dev.Send(MakeFrame(0x300, {0x01})).ok());
}

KM3_TEST(can_filters_can_be_applied_to_injection) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());
    // 只接收 0x180 段（CANopen PDO 常用段），掩码 0x780
    KM3_CHECK(dev.SetFilters({{0x180, 0x780}}).ok());
    dev.ApplyFiltersOnInject(true);

    dev.InjectRx(MakeFrame(0x181, {0x01}));   // 通过
    dev.InjectRx(MakeFrame(0x280, {0x02}));   // 被挡
    KM3_CHECK_EQ(dev.PendingRxCount(), std::size_t{1});

    auto r = dev.Receive(0);
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().id, std::uint32_t{0x181});
}

KM3_TEST(can_send_failure_is_counted) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());
    dev.Faults().fail_send = true;
    KM3_CHECK(!dev.Send(MakeFrame(0x400, {0x01})).ok());
    KM3_CHECK_EQ(dev.Stats().tx_errors, std::uint64_t{1});
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{0});
}

KM3_TEST(closed_can_device_rejects_operations) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    // 未打开
    KM3_CHECK(!dev.Send(MakeFrame(0x500, {0x01})).ok());
    KM3_CHECK(!dev.Receive(0).ok());
    KM3_CHECK(dev.StatusOf().state == km3::DeviceStatusEnum::Closed);
}

KM3_TEST(can_open_failure_is_reported) {
    MockCanDevice dev("can-1", CanBackend::SocketCan);
    dev.Faults().fail_open = true;
    const km3::Status s = dev.Open();
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == ErrorCode::EDeviceError);
    KM3_CHECK(!dev.IsOpen());
}

// ======================= RS485 =======================

KM3_TEST(rs485_modbus_read_returns_registers) {
    SerialConfig cfg;
    cfg.baudrate = 115200;
    MockRs485Device dev("usb-rs485-1", Rs485Backend::UsbSerial, cfg);
    KM3_CHECK(dev.Open().ok());

    dev.SetRegister(0x0000, 0x1234);
    dev.SetRegister(0x0001, 0x5678);

    auto r = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0x0000, 2);
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().registers.size(), std::size_t{2});
    KM3_CHECK_EQ(r.value().registers[0], std::uint16_t{0x1234});
    KM3_CHECK_EQ(r.value().registers[1], std::uint16_t{0x5678});
    KM3_CHECK_EQ(dev.Stats().rx_transactions, std::uint64_t{1});
}

KM3_TEST(rs485_modbus_write_then_read_reflects_value) {
    MockRs485Device dev("rs485-1", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    KM3_CHECK(dev.ModbusWrite(1, ModbusFunction::WriteSingleRegister, 0x0010,
                              {0xABCD}).ok());
    auto r = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0x0010, 1);
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().registers[0], std::uint16_t{0xABCD});
}

KM3_TEST(rs485_timeout_is_distinct_from_crc_error) {
    MockRs485Device dev("rs485-2", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    // 从站无应答：应报超时。现场表现是"设备不在线或没上电"。
    dev.Faults().timeout = true;
    auto t = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0, 1);
    KM3_CHECK(!t.ok());
    KM3_CHECK(t.code() == ErrorCode::ECommandTimeout);
    KM3_CHECK_EQ(dev.Stats().timeouts, std::uint64_t{1});

    // 有应答但校验错：应报校验失败。现场表现是电气/干扰问题。
    // 两者处理方式不同，因此必须能区分。
    dev.Faults().timeout = false;
    dev.Faults().crc_error = true;
    auto c = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0, 1);
    KM3_CHECK(!c.ok());
    KM3_CHECK(c.code() == ErrorCode::EVerifyFailed);
    KM3_CHECK_EQ(dev.Stats().crc_errors, std::uint64_t{1});
}

KM3_TEST(rs485_rejects_out_of_range_register) {
    MockRs485Device dev("rs485-1", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());
    auto r = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 250, 20);
    KM3_CHECK(!r.ok());
    KM3_CHECK(r.code() == ErrorCode::EInvalidRequest);
}

KM3_TEST(rs485_transact_records_request_and_returns_canned_response) {
    MockRs485Device dev("rs485-1", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    // 模拟雷赛 L7P 之类私有协议：请求由调用方构造
    dev.SetBytes({0x01, 0x03, 0x02, 0x00, 0x64, 0xB9, 0xAF});
    const std::vector<std::uint8_t> req = {0x01, 0x03, 0x00, 0x00, 0x00, 0x01, 0x84, 0x0A};

    auto r = dev.Transact(req, 7);
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().size(), std::size_t{7});
    KM3_CHECK_EQ(r.value()[4], std::uint8_t{0x64});
    // 请求必须被完整记录，便于断言上层发的字节
    KM3_CHECK_EQ(dev.SentBytes().size(), std::size_t{1});
    KM3_CHECK_EQ(dev.SentBytes()[0].size(), std::size_t{8});
}

KM3_TEST(rs485_write_only_has_no_response_counted) {
    MockRs485Device dev("usb-rs485-4", Rs485Backend::UsbSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    // 继电器模块是纯开关量输出，没有反馈
    KM3_CHECK(dev.WriteOnly({0x01, 0x06, 0x00, 0x01, 0x00, 0x01}).ok());
    KM3_CHECK_EQ(dev.Stats().tx_transactions, std::uint64_t{1});
    KM3_CHECK_EQ(dev.Stats().rx_transactions, std::uint64_t{0});
}

KM3_TEST(rs485_latency_is_reported_for_cycle_budget_check) {
    MockRs485Device dev("rs485-1", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    // 资料估算：115200 半双工下单个 Modbus 事务约 800–1000us，
    // 本身就接近 1ms 控制周期。这里用一个量级接近的值验证该指标可观测。
    dev.Faults().latency_us = 900;
    auto r = dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0, 2);
    KM3_CHECK(r.ok());
    KM3_CHECK_EQ(r.value().round_trip_us, std::uint64_t{900});
}

KM3_TEST(rs485_clock_tracks_last_success) {
    MockRs485Device dev("rs485-1", Rs485Backend::NativeSerial, SerialConfig{});
    KM3_CHECK(dev.Open().ok());

    const std::uint64_t before = km3::MonotonicUs();
    KM3_CHECK(dev.ModbusRead(1, ModbusFunction::ReadHoldingRegisters, 0, 1).ok());
    const std::uint64_t after = km3::MonotonicUs();

    const std::uint64_t last = dev.StatusOf().last_ok_us;
    KM3_CHECK(last >= before);
    KM3_CHECK(last <= after);
}

KM3_TEST(hal_kind_and_name_are_exposed_for_config_mapping) {
    // 逻辑名是配置与日志的对应键；物理设备路径不进入业务代码。
    MockCanDevice can("can-1", CanBackend::SocketCan);
    MockRs485Device rs("usb-rs485-3", Rs485Backend::UsbSerial, SerialConfig{});

    KM3_CHECK(can.Kind() == km3::DeviceKind::Can);
    KM3_CHECK(rs.Kind() == km3::DeviceKind::Rs485);
    KM3_CHECK_EQ(can.LogicalName(), std::string("can-1"));
    KM3_CHECK_EQ(rs.LogicalName(), std::string("usb-rs485-3"));
    KM3_CHECK_EQ(rs.Backend(), Rs485Backend::UsbSerial);
}

KM3_TEST_MAIN()
