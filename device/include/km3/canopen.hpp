#pragma once
// Non-RT, single-owner CANopen SDO executor. No heap storage or filter changes.
// Wire/state definitions are generated from tools/hwtui/canopen_wire.json.
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

#include "km3/canopen_wire_generated.hpp"
#include "km3/clock.hpp"
#include "km3/hal/can_device.hpp"

namespace km3::canopen {

struct Payload {
    static constexpr std::size_t kCapacity = wire::SDO_PAYLOAD_CAPACITY;
    std::array<std::uint8_t, kCapacity> bytes{};
    std::size_t size = 0;
};
struct TransferStats {
    std::uint64_t tx_frames = 0;
    std::uint64_t rx_frames = 0;
    std::uint64_t timeouts = 0;
};
inline std::uint32_t Decode32(const std::uint8_t* p) noexcept {
    return std::uint32_t{p[0]} | (std::uint32_t{p[1]} << 8u) |
           (std::uint32_t{p[2]} << 16u) | (std::uint32_t{p[3]} << 24u);
}
inline void Encode32(std::uint8_t* p, std::uint32_t value) noexcept {
    for (unsigned i = 0; i < 4; ++i) p[i] = static_cast<std::uint8_t>(value >> (i * 8u));
}
inline bool StateIs(std::uint16_t sw, std::uint16_t expected) noexcept {
    return (sw & wire::CIA402_MASK) == expected;
}
inline bool Fault(std::uint16_t sw) noexcept {
    return (sw & wire::CIA402_DISABLED_MASK) == wire::CIA402_FAULT;
}
inline bool FaultReaction(std::uint16_t sw) noexcept {
    return (sw & wire::CIA402_DISABLED_MASK) == wire::CIA402_FAULT_REACTION;
}
inline bool Disabled(std::uint16_t sw) noexcept {
    return (sw & wire::CIA402_DISABLED_MASK) == wire::CIA402_DISABLED;
}

class SdoClient {
public:
    explicit SdoClient(ICanDevice& device) noexcept : device_(device) {}
    TransferStats Stats() const noexcept { return stats_; }
    std::uint32_t LastAbortCode() const noexcept { return abort_code_; }
    bool IsQuarantined(std::uint8_t node) const noexcept {
        return node > 0 && node < quarantined_.size() && quarantined_[node];
    }
    // Explicit caller attestation only: independently recover the peer and
    // transport so no old response can still arrive, and discard queued frames.
    // A queue flush, NMT command, elapsed delay, or new client alone cannot prove
    // recovery: SDO has no transaction sequence number. Same-node SDO safety
    // cleanup is also blocked while quarantined; use independent safety control.
    Status ConfirmExternalRecovery(std::uint8_t node) noexcept {
        if (node == 0 || node >= quarantined_.size()) return Status(ErrorCode::EInvalidRequest);
        quarantined_[node] = false;
        return Status::Ok();
    }

    Status Nmt(std::uint8_t command, std::uint8_t node) noexcept {
        if (node > 127 || !(command == 1 || command == 2 || command == 128 ||
                            command == 129 || command == 130)) return Status(ErrorCode::EInvalidRequest);
        CanFrame frame;
        frame.dlc = 2;
        frame.data[0] = command;
        frame.data[1] = node;
        return Send(frame);
    }

    // deadline is one absolute CLOCK_MONOTONIC budget for the complete transfer.
    // The caller exclusively owns the receive queue. Other COB-IDs are consumed.
    Result<Payload> Upload(std::uint8_t node, std::uint16_t index, std::uint8_t sub,
                           std::uint64_t deadline) {
        auto st = Begin(node, index, sub, deadline);
        if (!st) return Result<Payload>::Err(st.code());
        auto request = Request(wire::SDO_CS_UPLOAD_REQUEST);
        st = Send(request);
        if (!st) return Result<Payload>::Err(st.code());
        auto reply = Receive(true);
        if (!reply) return Result<Payload>::Err(reply.code());
        const auto& data = reply.value().data;
        const auto cs = data[0];
        Payload result;
        if ((cs & wire::SDO_EXPEDITED_CS_MASK) == wire::SDO_CS_UPLOAD_EXPEDITED_BASE) {
            result.size = 4u - ((cs & wire::SDO_EXPEDITED_NBYTES_MASK) >> 2u);
            std::copy_n(data.begin() + 4, result.size, result.bytes.begin());
            return Result<Payload>::Ok(result);
        }
        if (cs != wire::SDO_CS_UPLOAD_SEGMENTED && cs != wire::SDO_CS_UPLOAD_SEGMENTED_NO_SIZE)
            return UploadError(0x05040001u);
        const bool known_size = (cs & 1u) != 0;
        const auto total = Decode32(data.data() + 4);
        if (known_size && total > Payload::kCapacity) return UploadError(0x05040005u);
        std::uint8_t toggle = 0;
        // Each non-final segment adds 7 bytes, so this is bounded even without size.
        for (std::size_t segment = 0; segment <= Payload::kCapacity / 7; ++segment) {
            request = Request(static_cast<std::uint8_t>(wire::SDO_CS_UPLOAD_SEGMENT_REQUEST | toggle));
            request.data[1] = request.data[2] = request.data[3] = 0;
            st = Send(request);
            if (!st) return Result<Payload>::Err(st.code());
            reply = Receive(false);
            if (!reply) return Result<Payload>::Err(reply.code());
            const auto& part = reply.value().data;
            const auto cmd = part[0];
            if ((cmd & wire::SDO_CS_UPLOAD_SEGMENT_MASK) != wire::SDO_CS_UPLOAD_SEGMENT_RESPONSE)
                return UploadError(0x05040001u);
            if ((cmd & wire::SDO_CS_SEGMENT_TOGGLE) != toggle) return UploadError(0x05030000u);
            const bool last = (cmd & wire::SDO_CS_SEGMENT_LAST) != 0;
            const std::size_t unused = (cmd & wire::SDO_CS_SEGMENT_NBYTES_MASK) >> 1u;
            if (!last && unused != 0) return UploadError(0x05040001u);
            const std::size_t count = 7 - unused;
            if (result.size + count > Payload::kCapacity ||
                (known_size && result.size + count > total)) return UploadError(0x06070012u);
            std::copy_n(part.begin() + 1, count, result.bytes.begin() + static_cast<std::ptrdiff_t>(result.size));
            result.size += count;
            if (last) {
                if (known_size && result.size != total) return UploadError(0x06070013u);
                return Result<Payload>::Ok(result);
            }
            toggle ^= wire::SDO_CS_SEGMENT_TOGGLE;
        }
        return UploadError(0x05040005u);
    }

    Status Download(std::uint8_t node, std::uint16_t index, std::uint8_t sub,
                    const std::uint8_t* bytes, std::size_t size, std::uint64_t deadline) {
        if (size == 0 || size > Payload::kCapacity || bytes == nullptr) return Status(ErrorCode::EInvalidRequest);
        auto st = Begin(node, index, sub, deadline);
        if (!st) return st;
        auto request = Request(static_cast<std::uint8_t>(size <= 4 ?
            wire::SDO_CS_DOWNLOAD_EXPEDITED | ((4 - size) << 2u) : wire::SDO_CS_DOWNLOAD_SEGMENTED));
        if (size <= 4) std::copy_n(bytes, size, request.data.begin() + 4);
        else Encode32(request.data.data() + 4, static_cast<std::uint32_t>(size));
        st = Send(request);
        if (!st) return st;
        auto reply = Receive(true);
        if (!reply) return Status(reply.code());
        if (reply.value().data[0] != wire::SDO_CS_DOWNLOAD_INITIATE_ACK) return Fail(0x05040001u);
        if (size <= 4) return Status::Ok();
        std::size_t offset = 0;
        std::uint8_t toggle = 0;
        while (offset < size) {
            const auto count = std::min<std::size_t>(7, size - offset);
            const bool last = offset + count == size;
            request = Request(toggle);
            request.data.fill(0);
            request.data[0] = static_cast<std::uint8_t>(toggle | (last ? 1u | ((7 - count) << 1u) : 0u));
            std::copy_n(bytes + offset, count, request.data.begin() + 1);
            st = Send(request);
            if (!st) return st;
            reply = Receive(false);
            if (!reply) return Status(reply.code());
            if (reply.value().data[0] != (wire::SDO_CS_DOWNLOAD_SEGMENT_ACK | toggle))
                return Fail(0x05030000u);
            offset += count;
            toggle ^= wire::SDO_CS_SEGMENT_TOGGLE;
        }
        return Status::Ok();
    }

private:
    Status Send(const CanFrame& frame) noexcept {
        if (frame.id != 0 && frame.data[0] != wire::SDO_CS_ABORT && MonotonicUs() >= deadline_) return Timeout();
        const auto st = device_.Send(frame);
        if (st) {
            ++stats_.tx_frames;
            if (frame.id != 0 && frame.data[0] != wire::SDO_CS_ABORT) transmitted_ = true;
        }
        return st;
    }
    CanFrame Request(std::uint8_t command) const noexcept {
        CanFrame frame;
        frame.id = wire::COB_SDO_REQUEST + node_;
        frame.dlc = 8;
        frame.data[0] = command;
        frame.data[1] = static_cast<std::uint8_t>(index_);
        frame.data[2] = static_cast<std::uint8_t>(index_ >> 8u);
        frame.data[3] = sub_;
        return frame;
    }
    Status Begin(std::uint8_t node, std::uint16_t index, std::uint8_t sub, std::uint64_t deadline) {
        abort_code_ = 0;
        if (node == 0 || node > 127 || index == 0xFFFFu) return Status(ErrorCode::EInvalidRequest);
        if (quarantined_[node]) return Status(ErrorCode::EDeviceBusy);
        if (!device_.IsOpen()) return Status(ErrorCode::EDeviceOffline);
        node_ = node; index_ = index; sub_ = sub; deadline_ = deadline;
        transmitted_ = false;
        if (MonotonicUs() >= deadline_) return Timeout();
        // Discard unsolicited queued traffic at session start. Timeout recovery
        // relies on quarantine above, never on this drain distinguishing late data.
        for (unsigned i = 0; i < 256; ++i) {
            auto old = device_.Receive(0);
            if (!old) return old.code() == ErrorCode::ECommandTimeout ? Status::Ok() : Status(old.code());
            ++stats_.rx_frames;
            if (MonotonicUs() >= deadline_) return Timeout();
        }
        return Status(ErrorCode::EDeviceBusy);
    }
    Status Timeout() noexcept {
        ++stats_.timeouts;
        if (transmitted_) quarantined_[node_] = true;
        return Status(ErrorCode::ECommandTimeout);
    }
    Status Fail(std::uint32_t code) noexcept {
        auto frame = Request(wire::SDO_CS_ABORT);
        Encode32(frame.data.data() + 4, code);
        (void)Send(frame); // Best effort cleanup must not hide the original failure.
        return Status(ErrorCode::EDeviceError);
    }
    Result<Payload> UploadError(std::uint32_t code) noexcept {
        return Result<Payload>::Err(Fail(code).code());
    }
    Result<CanFrame> Receive(bool echo) {
        for (;;) {
            const auto now = MonotonicUs();
            if (now >= deadline_) {
                (void)Fail(0x05040000u);
                return Result<CanFrame>::Err(Timeout().code());
            }
            const auto remaining = (deadline_ - now + 999u) / 1000u;
            auto result = device_.Receive(static_cast<int>(std::min<std::uint64_t>(remaining, 2147483647u)));
            if (!result) {
                if (result.code() == ErrorCode::ECommandTimeout) continue;
                return result;
            }
            ++stats_.rx_frames;
            const auto& frame = result.value();
            if (frame.id != wire::COB_SDO_RESPONSE + node_ || frame.extended || frame.remote || frame.fd) continue;
            if (MonotonicUs() >= deadline_) continue;
            if (frame.dlc != 8) return Result<CanFrame>::Err(Fail(0x05040001u).code());
            const bool abort = frame.data[0] == wire::SDO_CS_ABORT;
            if ((echo || abort) && (frame.data[1] != static_cast<std::uint8_t>(index_) ||
                frame.data[2] != static_cast<std::uint8_t>(index_ >> 8u) || frame.data[3] != sub_)) continue;
            if (abort) {
                abort_code_ = Decode32(frame.data.data() + 4);
                return Result<CanFrame>::Err(ErrorCode::ECommandRejected);
            }
            return result;
        }
    }
    ICanDevice& device_;
    TransferStats stats_{};
    std::uint32_t abort_code_ = 0;
    std::uint8_t node_ = 0, sub_ = 0;
    std::uint16_t index_ = 0;
    std::uint64_t deadline_ = 0;
    std::array<bool, 128> quarantined_{};
    bool transmitted_ = false;
};
}  // namespace km3::canopen
