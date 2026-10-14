#include "km3/hal/mock_devices.hpp"

#include <thread>

#include "km3/clock.hpp"

namespace km3 {

namespace {

// 模拟耗时。集中一处，避免每个方法各写一遍。
void SimulateLatency(const FaultInjection& f) noexcept {
    if (f.latency_us > 0) {
        std::this_thread::sleep_for(std::chrono::microseconds(f.latency_us));
    }
}

// ---- Modbus RTU 请求编码 ----
//
// 记录**真实线序的字节**，而不是把参数原样存下来。差别很实际：
// 若 SentBytes 只是参数的别名，"上层到底发了什么帧"就无法被逐字节断言，
// CRC 算错、高低字节序写反这类缺陷会全部漏过。
// CRC-16/MODBUS：init 0xFFFF、反射多项式 0xA001、无最终异或、**低字节先发**。
void AppendModbusCrc(std::vector<std::uint8_t>& frame) noexcept {
    std::uint16_t crc = 0xFFFF;
    for (std::uint8_t b : frame) {
        crc ^= b;
        for (int i = 0; i < 8; ++i) {
            crc = (crc & 1u) ? static_cast<std::uint16_t>((crc >> 1) ^ 0xA001u)
                             : static_cast<std::uint16_t>(crc >> 1);
        }
    }
    frame.push_back(static_cast<std::uint8_t>(crc & 0xFF));
    frame.push_back(static_cast<std::uint8_t>((crc >> 8) & 0xFF));
}

std::vector<std::uint8_t> EncodeModbusRead(std::uint8_t addr, std::uint8_t func,
                                           std::uint16_t reg, std::uint16_t count) {
    std::vector<std::uint8_t> f = {addr, func,
                                   static_cast<std::uint8_t>((reg >> 8) & 0xFF),
                                   static_cast<std::uint8_t>(reg & 0xFF),
                                   static_cast<std::uint8_t>((count >> 8) & 0xFF),
                                   static_cast<std::uint8_t>(count & 0xFF)};
    AppendModbusCrc(f);
    return f;
}

std::vector<std::uint8_t> EncodeModbusWrite(std::uint8_t addr, std::uint8_t func,
                                            std::uint16_t reg,
                                            const std::vector<std::uint16_t>& values) {
    std::vector<std::uint8_t> f = {addr, func,
                                   static_cast<std::uint8_t>((reg >> 8) & 0xFF),
                                   static_cast<std::uint8_t>(reg & 0xFF)};
    if (func == static_cast<std::uint8_t>(ModbusFunction::WriteSingleRegister)) {
        const std::uint16_t v = values.empty() ? 0 : values.front();
        f.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
        f.push_back(static_cast<std::uint8_t>(v & 0xFF));
    } else {
        const std::uint16_t n = static_cast<std::uint16_t>(values.size());
        f.push_back(static_cast<std::uint8_t>((n >> 8) & 0xFF));
        f.push_back(static_cast<std::uint8_t>(n & 0xFF));
        f.push_back(static_cast<std::uint8_t>((n * 2) & 0xFF));
        for (std::uint16_t v : values) {
            f.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
            f.push_back(static_cast<std::uint8_t>(v & 0xFF));
        }
    }
    AppendModbusCrc(f);
    return f;
}

bool MatchesAny(const std::vector<CanFilter>& filters, std::uint32_t id) noexcept {
    for (const auto& f : filters) {
        if ((id & f.mask) == (f.id & f.mask)) {
            return true;
        }
    }
    return false;
}

}  // namespace

// ============================ MockCanDevice ============================

MockCanDevice::MockCanDevice(std::string logical_name, CanBackend backend)
    : name_(std::move(logical_name)), backend_(backend) {
}

Status MockCanDevice::Open() noexcept {
    if (faults_.fail_open) {
        return Status(ErrorCode::EDeviceError);
    }
    open_ = true;
    return Status::Ok();
}

Status MockCanDevice::Close() noexcept {
    open_ = false;
    return Status::Ok();
}

DeviceStatus MockCanDevice::StatusOf() const {
    DeviceStatus st;
    st.state = !open_ ? DeviceStatusEnum::Closed
                      : (faults_.bus_off ? DeviceStatusEnum::BusOff : DeviceStatusEnum::Open);
    st.last_ok_us = stats_.last_error_us;
    st.error_count = stats_.tx_errors + stats_.rx_errors;
    if (faults_.bus_off) {
        st.detail = "bus off (injected)";
    }
    return st;
}

CanCapabilities MockCanDevice::Capabilities() const {
    CanCapabilities c;
    // 关键能力差异：主板 SocketCAN 不支持 CAN FD，USB 转 CAN 支持。
    c.supports_fd = (backend_ == CanBackend::UsbCan);
    c.max_bitrate = 1000000;
    c.supports_extended_id = true;
    c.supports_remote = true;
    return c;
}

Status MockCanDevice::Send(const CanFrame& frame) noexcept {
    if (!open_) {
        return Status(ErrorCode::EDeviceOffline);
    }
    if (faults_.fail_send || faults_.bus_off) {
        ++stats_.tx_errors;
        stats_.last_error_us = MonotonicUs();
        return Status(ErrorCode::EDeviceError);
    }
    // 能力校验：FD 帧发到经典后端必须被拒绝，而不是静默丢帧。
    if (frame.fd && !Capabilities().supports_fd) {
        ++stats_.tx_errors;
        stats_.last_error_us = MonotonicUs();
        return Status(ErrorCode::EInvalidRequest);
    }

    // 注：FD 帧的载荷长度校验由上面的 IsDlcValid() 覆盖（上限随 fd 变化）。
    // 校验基于原始 dlc，不用任何钳制后的值 —— 否则越界帧会被静默放行。
    if (!frame.IsDlcValid()) {
        ++stats_.tx_errors;
        stats_.last_error_us = MonotonicUs();
        return Status(ErrorCode::EInvalidRequest);
    }

    SimulateLatency(faults_);
    sent_.push_back(frame);
    ++stats_.tx_frames;
    stats_.last_error_us = MonotonicUs();
    return Status::Ok();
}

Result<CanFrame> MockCanDevice::Receive(int /*timeout_ms*/) {
    if (!open_) {
        return Result<CanFrame>::Err(ErrorCode::EDeviceOffline);
    }
    if (faults_.bus_off) {
        return Result<CanFrame>::Err(ErrorCode::EDeviceError);
    }
    if (rx_.empty()) {
        // 总线空闲是正常状态，不是错误 —— 调用方据此决定是否继续轮询。
        return Result<CanFrame>::Err(ErrorCode::ECommandTimeout);
    }
    CanFrame frame = rx_.front();
    rx_.pop_front();
    ++stats_.rx_frames;
    return Result<CanFrame>::Ok(frame);
}

Status MockCanDevice::SetFilters(const std::vector<CanFilter>& filters) {
    filters_ = filters;
    return Status::Ok();
}

CanBusStats MockCanDevice::Stats() const { return stats_; }

Status MockCanDevice::RecoverFromBusOff() {
    if (!faults_.bus_off) {
        return Status(ErrorCode::EInvalidRequest);
    }
    faults_.bus_off = false;
    ++stats_.bus_off_count;
    return Status::Ok();
}

void MockCanDevice::InjectRx(const CanFrame& frame) {
    if (apply_filters_ && !filters_.empty() && !MatchesAny(filters_, frame.id)) {
        return;   // 被过滤器挡掉
    }
    rx_.push_back(frame);
}

// =========================== MockRs485Device ===========================

MockRs485Device::MockRs485Device(std::string logical_name, Rs485Backend backend,
                                 SerialConfig cfg)
    : name_(std::move(logical_name)), backend_(backend), cfg_(cfg) {
    registers_.assign(256, 0);
}

Status MockRs485Device::Open() noexcept {
    if (faults_.fail_open) {
        return Status(ErrorCode::EDeviceError);
    }
    open_ = true;
    return Status::Ok();
}

Status MockRs485Device::Close() noexcept {
    open_ = false;
    return Status::Ok();
}

DeviceStatus MockRs485Device::StatusOf() const {
    DeviceStatus st;
    st.state = open_ ? DeviceStatusEnum::Open : DeviceStatusEnum::Closed;
    st.last_ok_us = stats_.last_ok_us;
    st.error_count = stats_.timeouts + stats_.crc_errors + stats_.framing_errors;
    return st;
}

Result<ModbusResponse> MockRs485Device::ModbusRead(std::uint8_t slave_addr,
                                                   ModbusFunction func,
                                                   std::uint16_t register_addr,
                                                   std::uint16_t count) {
    if (!open_) {
        return Result<ModbusResponse>::Err(ErrorCode::EDeviceOffline);
    }
    // 记录发生在故障判定**之前**：超时与 CRC 错也意味着请求确实上了线，
    // 记成"没发过"会让排障时看不到真实的总线活动。
    sent_bytes_.push_back(EncodeModbusRead(slave_addr,
                                          static_cast<std::uint8_t>(func),
                                          register_addr, count));
    ++stats_.tx_transactions;
    SimulateLatency(faults_);

    if (faults_.timeout) {
        ++stats_.timeouts;
        return Result<ModbusResponse>::Err(ErrorCode::ECommandTimeout);
    }
    if (faults_.crc_error) {
        ++stats_.crc_errors;
        return Result<ModbusResponse>::Err(ErrorCode::EVerifyFailed);
    }

    if (register_addr + count > registers_.size()) {
        return Result<ModbusResponse>::Err(ErrorCode::EInvalidRequest);
    }

    ModbusResponse resp;
    resp.registers.reserve(count);
    for (std::uint16_t i = 0; i < count; ++i) {
        resp.registers.push_back(registers_[register_addr + i]);
    }
    // 往返耗时如实反映配置的模拟延迟：用于评估 115200 半双工事务能否装进 1ms 周期。
    resp.round_trip_us = faults_.latency_us;
    ++stats_.rx_transactions;
    stats_.last_ok_us = MonotonicUs();
    return Result<ModbusResponse>::Ok(std::move(resp));
}

Status MockRs485Device::ModbusWrite(std::uint8_t slave_addr, ModbusFunction func,
                                    std::uint16_t register_addr,
                                    const std::vector<std::uint16_t>& values) {
    if (!open_) {
        return Status(ErrorCode::EDeviceOffline);
    }
    sent_bytes_.push_back(EncodeModbusWrite(
        slave_addr, static_cast<std::uint8_t>(func), register_addr, values));
    ++stats_.tx_transactions;
    SimulateLatency(faults_);

    if (faults_.timeout) {
        ++stats_.timeouts;
        return Status(ErrorCode::ECommandTimeout);
    }
    if (register_addr + values.size() > registers_.size()) {
        return Status(ErrorCode::EInvalidRequest);
    }
    for (std::size_t i = 0; i < values.size(); ++i) {
        registers_[register_addr + i] = values[i];
    }
    ++stats_.rx_transactions;
    stats_.last_ok_us = MonotonicUs();
    return Status::Ok();
}

Result<std::vector<std::uint8_t>> MockRs485Device::Transact(
    const std::vector<std::uint8_t>& request, std::size_t expected_response_len) {
    if (!open_) {
        return Result<std::vector<std::uint8_t>>::Err(ErrorCode::EDeviceOffline);
    }
    sent_bytes_.push_back(request);
    ++stats_.tx_transactions;
    SimulateLatency(faults_);

    if (faults_.timeout) {
        ++stats_.timeouts;
        return Result<std::vector<std::uint8_t>>::Err(ErrorCode::ECommandTimeout);
    }
    if (faults_.crc_error) {
        ++stats_.crc_errors;
        return Result<std::vector<std::uint8_t>>::Err(ErrorCode::EVerifyFailed);
    }

    std::vector<std::uint8_t> resp = canned_response_;
    resp.resize(expected_response_len, 0);   // 长度不符时按期望补零，便于测上层解析
    ++stats_.rx_transactions;
    stats_.last_ok_us = MonotonicUs();
    return Result<std::vector<std::uint8_t>>::Ok(std::move(resp));
}

Status MockRs485Device::WriteOnly(const std::vector<std::uint8_t>& bytes) {
    if (!open_) {
        return Status(ErrorCode::EDeviceOffline);
    }
    sent_bytes_.push_back(bytes);
    ++stats_.tx_transactions;
    SimulateLatency(faults_);
    if (faults_.fail_send) {
        return Status(ErrorCode::EDeviceError);
    }
    // 单向写没有应答，因此不计 rx。
    stats_.last_ok_us = MonotonicUs();
    return Status::Ok();
}

Rs485BusStats MockRs485Device::Stats() const { return stats_; }

void MockRs485Device::SetRegister(std::uint16_t register_addr, std::uint16_t value) noexcept {
    if (register_addr < registers_.size()) {
        registers_[register_addr] = value;
    }
}

void MockRs485Device::SetBytes(std::vector<std::uint8_t> response) {
    canned_response_ = std::move(response);
}

}  // namespace km3
