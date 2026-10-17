#pragma once
// Standard-layer PDO primitives. No manufacturer indices or motion policy here.
#include <array>
#include <cstddef>
#include <cstdint>

#include "km3/canopen_sync.hpp"
#include "km3/spsc_ring.hpp"

namespace km3::canopen {

inline constexpr std::size_t kMaxPdos = 32;
struct PdoMapping {
    std::uint32_t cob_id = 0;
    // Read/generate :00 entry count, then ONLY those mapping words (:01..count).
    std::uint8_t entry_count = 0;
    std::array<std::uint32_t, 8> entries{};
};
struct PdoValues {
    // Raw two's-complement object bits. Signed interpretation belongs to the OD.
    std::array<std::uint32_t, 8> values{};
    std::uint8_t count = 0;
};
Result<std::uint8_t> PdoLength(const PdoMapping& mapping) noexcept;
Result<CanFrame> EncodePdo(const PdoMapping& mapping, const PdoValues& values) noexcept;
Result<PdoValues> DecodePdo(const PdoMapping& mapping, const CanFrame& frame) noexcept;

struct PdoConfig {
    std::array<PdoMapping, kMaxPdos> rpdo{}, tpdo{};
    std::size_t rpdo_count = 0, tpdo_count = 0;
    std::uint32_t sync_cob_id = wire::COB_SYNC;
};
struct PdoBatch {
    std::array<PdoValues, kMaxPdos> values{};
    std::size_t count = 0;
};
struct TpdoSample {
    PdoValues values{};
    std::uint64_t processed_us = 0;
    std::uint32_t cob_id = 0;
};
struct PdoStats {
    std::uint64_t rpdo_sent = 0, failures = 0, tpdo_received = 0;
    std::uint64_t malformed = 0, rx_errors = 0, unrelated = 0;
};

// SCHED_OTHER cycle delegate of SyncWorker: one sender and one receive owner.
// Scheduling jitter is NOT qualified for CSP: absolute phase/skipping contain
// overruns but cannot bound Linux arbitration/scheduling or prove mechanical
// tracking. This implements transport; hardware timing acceptance remains open.
// Construct/Validate on boundary thread; Submit is the single producer (may be
// RT); BeforeSync/ReceiveAvailable only the worker; Pop is the single consumer.
class PdoSession final : public SyncCycle {
public:
    PdoSession(ICanDevice& device, const PdoConfig& config) noexcept
        : device_(device), config_(config) {}
    Status Validate() const noexcept;
    void LatchFailure() noexcept override { failed_ = true; }
    Status ValidateForSync(std::uint32_t cob_id) const noexcept override {
        return cob_id == config_.sync_cob_id ? Validate() : Status(ErrorCode::EConfigConflict);
    }
    Status Submit(const PdoBatch& batch) noexcept;
    bool Pop(TpdoSample& sample) noexcept { return feedback_.Pop(sample); }
    std::uint64_t CommandDrops() const noexcept { return commands_.Dropped(); }
    std::uint64_t FeedbackDrops() const noexcept { return feedback_.Dropped(); }
    // Inspect Stats only after worker join; ring counters above are atomic.
    PdoStats Stats() const noexcept { return stats_; }
    Status BeforeSync(std::uint64_t deadline_us) noexcept override;
    void ReceiveAvailable() noexcept override;

private:
    ICanDevice& device_;
    const PdoConfig config_;
    SpscRing<PdoBatch, 8> commands_{};
    SpscRing<TpdoSample, 64> feedback_{};
    PdoStats stats_{};
    bool failed_ = false;
};

}  // namespace km3::canopen
