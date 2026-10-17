#include "km3/canopen_pdo.hpp"

namespace km3::canopen {

Result<std::uint8_t> PdoLength(const PdoMapping& mapping) noexcept {
    if (mapping.cob_id == 0 || mapping.cob_id > 0x7FF || mapping.entry_count == 0 ||
        mapping.entry_count > mapping.entries.size())
        return Result<std::uint8_t>::Err(ErrorCode::EInvalidConfig);
    unsigned bits = 0;
    for (unsigned i = 0; i < mapping.entry_count; ++i) {
        const auto word = mapping.entries[i];
        const auto width = word & 0xFFu;
        if ((word >> 16u) == 0 || (width != 8 && width != 16 && width != 32))
            return Result<std::uint8_t>::Err(ErrorCode::EInvalidConfig);
        bits += width;
    }
    if (bits > 64)
        return Result<std::uint8_t>::Err(ErrorCode::EInvalidConfig);
    return Result<std::uint8_t>::Ok(static_cast<std::uint8_t>(bits / 8));
}

Result<CanFrame> EncodePdo(const PdoMapping& mapping, const PdoValues& values) noexcept {
    const auto length = PdoLength(mapping);
    if (!length)
        return Result<CanFrame>::Err(length.code());
    if (values.count != mapping.entry_count)
        return Result<CanFrame>::Err(ErrorCode::EInvalidRequest);
    CanFrame frame{};
    frame.id = mapping.cob_id;
    frame.dlc = length.value();
    unsigned offset = 0;
    for (unsigned i = 0; i < mapping.entry_count; ++i) {
        const auto bytes = (mapping.entries[i] & 0xFFu) / 8;
        const auto value = values.values[i];
        if (bytes < 4 && (value >> (bytes * 8)) != 0)
            return Result<CanFrame>::Err(ErrorCode::EInvalidRequest);
        for (unsigned b = 0; b < bytes; ++b)
            frame.data[offset++] = static_cast<std::uint8_t>(value >> (b * 8));
    }
    return Result<CanFrame>::Ok(frame);
}

Result<PdoValues> DecodePdo(const PdoMapping& mapping, const CanFrame& frame) noexcept {
    const auto length = PdoLength(mapping);
    if (!length)
        return Result<PdoValues>::Err(length.code());
    if (frame.fd || frame.remote || frame.extended || frame.bitrate_switch || !frame.IsDlcValid() ||
        frame.id != mapping.cob_id || frame.dlc != length.value())
        return Result<PdoValues>::Err(ErrorCode::EInvalidRequest);
    PdoValues values{};
    values.count = mapping.entry_count;
    unsigned offset = 0;
    for (unsigned i = 0; i < mapping.entry_count; ++i) {
        const auto bytes = (mapping.entries[i] & 0xFFu) / 8;
        for (unsigned b = 0; b < bytes; ++b)
            values.values[i] |= std::uint32_t{frame.data[offset++]} << (b * 8);
    }
    return Result<PdoValues>::Ok(values);
}

Status PdoSession::Validate() const noexcept {
    if (failed_ || config_.rpdo_count > kMaxPdos || config_.tpdo_count > kMaxPdos ||
        config_.sync_cob_id == 0 || config_.sync_cob_id > 0x7FF ||
        !device_.SupportsTxConfirmation())
        return Status(ErrorCode::EInvalidConfig);
    std::array<std::uint32_t, 2 * kMaxPdos + 1> ids{};
    std::size_t count = 0;
    ids[count++] = config_.sync_cob_id;
    for (unsigned direction = 0; direction < 2; ++direction) {
        const auto& maps = direction == 0 ? config_.rpdo : config_.tpdo;
        const auto size = direction == 0 ? config_.rpdo_count : config_.tpdo_count;
        for (std::size_t i = 0; i < size; ++i) {
            if (!PdoLength(maps[i]))
                return Status(ErrorCode::EInvalidConfig);
            for (std::size_t j = 0; j < count; ++j)
                if (ids[j] == maps[i].cob_id)
                    return Status(ErrorCode::EConfigConflict);
            ids[count++] = maps[i].cob_id;
        }
    }
    return Status::Ok();
}

Status PdoSession::Submit(const PdoBatch& batch) noexcept {
    if (config_.rpdo_count > kMaxPdos || batch.count != config_.rpdo_count)
        return Status(ErrorCode::EInvalidRequest);
    // Validate the complete transaction before any transmission or queue push.
    for (std::size_t i = 0; i < batch.count; ++i) {
        const auto frame = EncodePdo(config_.rpdo[i], batch.values[i]);
        if (!frame)
            return Status(frame.code());
    }
    return commands_.Push(batch) ? Status::Ok() : Status(ErrorCode::EDeviceBusy);
}

Status PdoSession::BeforeSync(std::uint64_t deadline_us) noexcept {
    if (failed_)
        return Status(ErrorCode::ECommandRejected);
    if (const auto valid = Validate(); !valid)
        return valid;
    PdoBatch batch{}, newest{};
    bool available = false;
    // Bound drain even if the producer keeps publishing; keep the newest of
    // this bounded snapshot, never replay an old command automatically.
    for (unsigned i = 0; i < 8 && commands_.Pop(batch); ++i) {
        newest = batch;
        available = true;
    }
    if (!available)
        return Status::Ok();
    for (std::size_t i = 0; i < newest.count; ++i) {
        const auto frame = EncodePdo(config_.rpdo[i], newest.values[i]);
        // Confirmed physical-driver completion, NOT socket/kernel queue success.
        // All RPDOs finish BEFORE SyncProducer may queue its lower-ID SYNC.
        const auto status = device_.SendConfirmed(frame.value(), deadline_us);
        if (!status) {
            failed_ = true;
            ++stats_.failures;
            return status;  // no SYNC for a partial/failed batch
        }
        ++stats_.rpdo_sent;
    }
    return Status::Ok();
}

void PdoSession::ReceiveAvailable() noexcept {
    // Missing type-0/event-driven/decimated TPDO is normal. Never wait or report
    // a per-cycle missing-data error. Each record carries its drain/processing timestamp, NOT bus
    // arrival; the control/safety owner must impose its separately validated freshness.
    for (unsigned budget = 0; budget < 64; ++budget) {
        const auto rx = device_.Receive(0);
        if (rx.code() == ErrorCode::ECommandTimeout)
            break;
        if (!rx) {
            ++stats_.rx_errors;
            continue;
        }
        bool matched = false;
        for (std::size_t i = 0; i < config_.tpdo_count && i < kMaxPdos; ++i) {
            if (config_.tpdo[i].cob_id != rx.value().id)
                continue;
            matched = true;
            const auto values = DecodePdo(config_.tpdo[i], rx.value());
            if (!values) {
                ++stats_.malformed;
                break;
            }
            ++stats_.tpdo_received;
            feedback_.Push({values.value(), MonotonicUs(), rx.value().id});
            break;
        }
        if (!matched)
            ++stats_.unrelated;
    }
}

}  // namespace km3::canopen
