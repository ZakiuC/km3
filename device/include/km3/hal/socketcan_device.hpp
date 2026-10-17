#pragma once
// Linux classic-CAN backend. Single-owner, non-RT API (IDevice contract).
#include <cstddef>
#include <linux/can.h>

#include "km3/config.hpp"
#include "km3/hal/can_device.hpp"

namespace km3 {

// Netlink telemetry is separate from application frame counters. Kernel counters
// are lifetime-of-interface values; do not add them to observed error frames.
struct SocketCanLinkInfo {
    bool is_can = false;
    bool up = false;
    bool driver_echo = false;
    bool has_bitrate = false;
    bool has_state = false;
    bool has_stats = false;
    std::uint32_t bitrate = 0;
    std::uint32_t state = 0;
    std::uint32_t bus_off = 0;
    std::uint32_t bus_error = 0;
    std::uint32_t error_warning = 0;
    std::uint32_t error_passive = 0;
    std::uint32_t restarts = 0;
    std::uint16_t tx_error_counter = 0;
    std::uint16_t rx_error_counter = 0;
};

class SocketCanDevice final : public ICanDevice {
public:
    explicit SocketCanDevice(BusEndpoint endpoint, bool confirm_tx = false);
    ~SocketCanDevice() override;
    const std::string& LogicalName() const noexcept override { return endpoint_.logical; }
    CanBackend Backend() const noexcept override { return CanBackend::SocketCan; }
    CanCapabilities Capabilities() const override { return {}; }
    Status Open() noexcept override;
    Status Close() noexcept override;
    bool IsOpen() const noexcept override { return fd_ >= 0; }
    DeviceStatus StatusOf() const override;
    // Confirmation mode permits only SendConfirmed; plain Send cannot leave
    // an unmatched completion that could acknowledge a later identical frame.
    Status Send(const CanFrame& frame) noexcept override;
    bool SupportsTxConfirmation() const noexcept override { return confirm_tx_; }
    Status SendConfirmed(const CanFrame& frame, std::uint64_t deadline_us) noexcept override;
    Result<CanFrame> Receive(int timeout_ms) override;
    Status SetFilters(const std::vector<CanFilter>& filters) override;
    CanBusStats Stats() const override;
    Status RecoverFromBusOff() override;
    // Last netlink snapshot; refreshed by Open, Stats, StatusOf and recovery.
    const SocketCanLinkInfo& LinkInfo() const noexcept { return link_info_; }
    ErrorCode LastLinkError() const noexcept { return link_error_; }
    int LastSystemError() const noexcept { return system_error_; }

    // Pure wire conversion, shared by live IO and hardware-free tests.
    static Status Encode(const CanFrame& frame, struct can_frame& raw) noexcept;
    static Result<CanFrame> Decode(const struct can_frame& raw) noexcept;
    static Status ParseLinkMessage(const void* bytes, std::size_t length, int ifindex,
                                   SocketCanLinkInfo& info) noexcept;
    // Error notifications never surface as data frames. Public for replay tests.
    void ObserveError(const struct can_frame& raw) noexcept;

private:
    Status RefreshLink() const noexcept;
    Status RecordTxError(ErrorCode code) noexcept;
    Status SendRaw(const CanFrame& frame) noexcept;
    BusEndpoint endpoint_;
    const bool confirm_tx_;
    bool tx_quarantined_ = false;
    std::array<CanFrame, 64> pending_rx_{};
    std::size_t pending_head_ = 0, pending_count_ = 0;
    int fd_ = -1;
    int ifindex_ = 0;
    std::vector<CanFilter> filters_;
    mutable CanBusStats stats_{};
    mutable SocketCanLinkInfo link_info_{};
    mutable int system_error_ = 0;
    mutable ErrorCode link_error_ = ErrorCode::Ok;
    mutable DeviceStatusEnum state_ = DeviceStatusEnum::Closed;
    std::uint64_t last_ok_us_ = 0;
    std::uint64_t observed_bus_off_ = 0;
};

}  // namespace km3
