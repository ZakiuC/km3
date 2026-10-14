#pragma once
// 模拟设备。见 docs/架构设计/设备端程序架构.md §3.3。
//
// 为什么必须有：实测中 CAN、USB-RS485、USB-CAN 模块**当前均未接入**
// （见 实测基线.md §五）。没有模拟设备，总线层与上层控制逻辑在硬件到位前
// 完全无法测试。它们不是"测试替身"的将就做法，而是设计的一部分。
//
// 能力：
//   - 注入接收帧/应答（模拟从站）
//   - 记录发送帧（断言上层发了什么）
//   - 故障开关（超时 / CRC 错误 / bus-off / 发送失败）
//   - 可调延迟（模拟真实往返时间，例如 115200 半双工 Modbus 事务约 800–1000us）

#include <cstdint>
#include <deque>
#include <string>
#include <vector>

#include "km3/hal/can_device.hpp"
#include "km3/hal/rs485_device.hpp"

namespace km3 {

// 故障注入开关。每个都对应一类真实故障，便于写针对性测试。
struct FaultInjection {
    bool fail_open = false;
    bool fail_send = false;
    bool timeout = false;          // 应收而永不应答
    bool crc_error = false;        // 应答 CRC 不符
    bool bus_off = false;          // 仅 CAN
    std::uint64_t latency_us = 0;  // 每次操作的模拟耗时
};

class MockCanDevice final : public ICanDevice {
public:
    MockCanDevice(std::string logical_name, CanBackend backend);

    // ---- IDevice ----
    DeviceKind Kind() const noexcept override { return DeviceKind::Can; }
    const std::string& LogicalName() const noexcept override { return name_; }
    Status Open() noexcept override;
    Status Close() noexcept override;
    bool IsOpen() const noexcept override { return open_; }
    DeviceStatus StatusOf() const override;

    // ---- ICanDevice ----
    CanBackend Backend() const noexcept override { return backend_; }
    CanCapabilities Capabilities() const override;
    Status Send(const CanFrame& frame) noexcept override;
    Result<CanFrame> Receive(int timeout_ms) override;
    Status SetFilters(const std::vector<CanFilter>& filters) override;
    CanBusStats Stats() const override;
    Status RecoverFromBusOff() override;

    // ---- 测试辅助 ----
    void InjectRx(const CanFrame& frame);
    const std::vector<CanFrame>& SentFrames() const noexcept { return sent_; }
    void ClearSent() noexcept { sent_.clear(); }
    FaultInjection& Faults() noexcept { return faults_; }
    void ApplyFiltersOnInject(bool enable) noexcept { apply_filters_ = enable; }
    // 总线上已有的帧数（用于断言"上层把队列读空了吗"）
    std::size_t PendingRxCount() const noexcept { return rx_.size(); }

private:
    std::string name_;
    CanBackend backend_;
    bool open_ = false;
    bool apply_filters_ = false;
    std::vector<CanFrame> sent_;
    std::deque<CanFrame> rx_;
    std::vector<CanFilter> filters_;
    CanBusStats stats_;
    FaultInjection faults_;
};

class MockRs485Device final : public IRs485Device {
public:
    MockRs485Device(std::string logical_name, Rs485Backend backend, SerialConfig cfg);

    // ---- IDevice ----
    DeviceKind Kind() const noexcept override { return DeviceKind::Rs485; }
    const std::string& LogicalName() const noexcept override { return name_; }
    Status Open() noexcept override;
    Status Close() noexcept override;
    bool IsOpen() const noexcept override { return open_; }
    DeviceStatus StatusOf() const override;

    // ---- IRs485Device ----
    Rs485Backend Backend() const noexcept override { return backend_; }
    const SerialConfig& Config() const noexcept override { return cfg_; }
    Result<ModbusResponse> ModbusRead(std::uint8_t slave_addr, ModbusFunction func,
                                      std::uint16_t register_addr,
                                      std::uint16_t count) override;
    Status ModbusWrite(std::uint8_t slave_addr, ModbusFunction func,
                       std::uint16_t register_addr,
                       const std::vector<std::uint16_t>& values) override;
    Result<std::vector<std::uint8_t>> Transact(const std::vector<std::uint8_t>& request,
                                               std::size_t expected_response_len) override;
    Status WriteOnly(const std::vector<std::uint8_t>& bytes) override;
    Rs485BusStats Stats() const override;

    // ---- 测试辅助：按寄存器地址预置 Modbus 应答 ----
    void SetRegister(std::uint16_t register_addr, std::uint16_t value) noexcept;
    void SetBytes(std::vector<std::uint8_t> response);          // Transact 的应答
    const std::vector<std::vector<std::uint8_t>>& SentBytes() const noexcept { return sent_bytes_; }
    void ClearSent() noexcept { sent_bytes_.clear(); }
    FaultInjection& Faults() noexcept { return faults_; }

private:
    std::string name_;
    Rs485Backend backend_;
    SerialConfig cfg_;
    bool open_ = false;
    std::vector<std::vector<std::uint8_t>> sent_bytes_;
    std::vector<std::uint16_t> registers_;   // 按地址下标存放
    std::vector<std::uint8_t> canned_response_;
    Rs485BusStats stats_;
    FaultInjection faults_;
};

}  // namespace km3
