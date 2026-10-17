#pragma once
// Model factory stays behind the unchanged global OD compile/runtime gates.
#include "km3/canopen_pdo.hpp"
#include "km3/config.hpp"
#include "od_generated.hpp"
#include <algorithm>
#include <iterator>

namespace km3::pmp4010 {
inline Result<canopen::PdoConfig> MakePdoConfig(std::uint8_t node, const RtParams& rt) noexcept {
    if (!od::kOdVerified)
        return Result<canopen::PdoConfig>::Err(ErrorCode::EInvalidConfig);
    if (node == 0 || node > 127 || rt.sync_cob_id == 0 || rt.sync_cob_id > 0x7FF ||
        rt.sync_period_us == 0 || rt.sync_period_us > kMaxSyncPeriodUs)
        return Result<canopen::PdoConfig>::Err(ErrorCode::EInvalidConfig);
    canopen::PdoConfig config{};
    config.sync_cob_id = rt.sync_cob_id;
    config.rpdo_count = std::size(od::kRpdoLayouts);
    config.tpdo_count = std::size(od::kTpdoLayouts);
    for (std::size_t i = 0; i < config.rpdo_count; ++i) {
        config.rpdo[i].cob_id = od::kRpdoLayouts[i].cob_base + node;
        config.rpdo[i].entry_count = od::kRpdoLayouts[i].entry_count;
        std::copy_n(od::kRpdoLayouts[i].entries, 8, config.rpdo[i].entries.begin());
    }
    for (std::size_t i = 0; i < config.tpdo_count; ++i) {
        config.tpdo[i].cob_id = od::kTpdoLayouts[i].cob_base + node;
        config.tpdo[i].entry_count = od::kTpdoLayouts[i].entry_count;
        std::copy_n(od::kTpdoLayouts[i].entries, 8, config.tpdo[i].entries.begin());
    }
    // Defaults are templates, not a live mapping attestation. The owner must
    // compare full :00/:01.. counts/words and communication IDs read from peer.
    return Result<canopen::PdoConfig>::Ok(config);
}
}  // namespace km3::pmp4010
