#pragma once
// RS485 设备抽象。见 docs/架构设计/设备端程序架构.md §2.1.4 与 §3.2。
//
// 两类后端：
//   NativeSerialBackend —— 主板原生隔离 RS485：RS485-1 = /dev/ttyS3，RS485-2 = /dev/ttyS5
//   UsbSerialBackend    —— USB-RS485 模块（4 路），承载超声/IMU/气压/温湿度/电源/继电器
//
// **半双工方向控制**是这一层最容易出错的地方（资料原文："收发控制 GPIO 需匹配"、
// "控制 GPIO 和串口由同一驱动/服务协调，不让多个进程竞争"）。
// 因此接口把"一次事务"作为原子操作暴露（Transact），由后端负责：
//   置发送方向 -> 写 -> 等待发送完成 -> 置接收方向 -> 读 -> 超时判定
// 调用方不得自行穿插读写，否则会出现半帧冲突。

#include <cstdint>
#include <string>
#include <vector>

#include "km3/hal/device.hpp"

namespace km3 {

enum class Rs485Backend : std::uint8_t {
    NativeSerial = 0,   // 主板 ttyS3 / ttyS5（隔离式半双工）
    UsbSerial = 1,      // USB-RS485 模块
};

std::string_view ToString(Rs485Backend backend);

struct SerialConfig {
    std::uint32_t baudrate = 115200;
    std::uint8_t data_bits = 8;
    std::uint8_t stop_bits = 1;
    char parity = 'N';                 // 'N' / 'E' / 'O'
    int read_timeout_ms = 100;

    // 半双工收发切换的稳定时间。切换过快会截断帧尾；
    // 该值随收发器型号与波特率变化，属部署参数。
    int direction_delay_us = 200;
};

// Modbus RTU 功能码。只列本项目用到的，不追求完整实现。
enum class ModbusFunction : std::uint8_t {
    ReadHoldingRegisters = 0x03,
    ReadInputRegisters = 0x04,
    WriteSingleRegister = 0x06,
    WriteMultipleRegisters = 0x10,
};

struct ModbusResponse {
    std::vector<std::uint16_t> registers;
    std::uint64_t round_trip_us = 0;   // 实测往返耗时，用于评估是否装得进控制周期
};

// 总线统计。半双工总线上，CRC 错误与超时是最常见的两类故障，需要分别计数。
struct Rs485BusStats {
    std::uint64_t tx_transactions = 0;
    std::uint64_t rx_transactions = 0;
    std::uint64_t timeouts = 0;
    std::uint64_t crc_errors = 0;
    std::uint64_t framing_errors = 0;
    std::uint64_t last_ok_us = 0;
};

class IRs485Device : public IDevice {
public:
    DeviceKind Kind() const noexcept override { return DeviceKind::Rs485; }

    virtual Rs485Backend Backend() const noexcept = 0;
    virtual const SerialConfig& Config() const noexcept = 0;

    // Modbus RTU 读。slave_addr 是站号，register_addr 是寄存器地址。
    // 返回 ECommandTimeout 表示从站未应答（总线空闲或设备不在线），
    // 返回 EVerifyFailed 表示应答 CRC 不符（电气/干扰问题）。
    virtual Result<ModbusResponse> ModbusRead(std::uint8_t slave_addr,
                                              ModbusFunction func,
                                              std::uint16_t register_addr,
                                              std::uint16_t count) = 0;

    virtual Status ModbusWrite(std::uint8_t slave_addr,
                               ModbusFunction func,
                               std::uint16_t register_addr,
                               const std::vector<std::uint16_t>& values) = 0;

    // 厂商私有协议（如雷赛 L7P 伺服）。请求与应答由调用方构造与解析，
    // 本层只保证"一次完整事务"的方向控制与超时语义。
    virtual Result<std::vector<std::uint8_t>> Transact(
        const std::vector<std::uint8_t>& request,
        std::size_t expected_response_len) = 0;

    // 单向写，用于只发不收的控制指令（如继电器通断、鼓风机启停）。
    virtual Status WriteOnly(const std::vector<std::uint8_t>& bytes) = 0;

    virtual Rs485BusStats Stats() const = 0;

protected:
    IRs485Device() = default;
};

}  // namespace km3
