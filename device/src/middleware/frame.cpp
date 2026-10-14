#include "km3/frame.hpp"

#include <cstring>

namespace km3 {

std::uint16_t Crc16Update(std::uint16_t crc, const std::uint8_t* data, std::size_t len) {
    // 分段累加：CRC 是无最终异或的线性状态机，连续两段等价于对拼接结果计算一次。
    for (std::size_t i = 0; i < len; ++i) {
        crc = static_cast<std::uint16_t>(crc ^ (static_cast<std::uint16_t>(data[i]) << 8));
        for (int bit = 0; bit < 8; ++bit) {
            if (crc & 0x8000) {
                crc = static_cast<std::uint16_t>((crc << 1) ^ 0x1021);
            } else {
                crc = static_cast<std::uint16_t>(crc << 1);
            }
        }
    }
    return crc;
}

namespace {

void PutU16(std::vector<std::uint8_t>& out, std::uint16_t v) {
    out.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));  // 网络字节序（大端）
    out.push_back(static_cast<std::uint8_t>(v & 0xFF));
}

std::uint16_t GetU16(const std::uint8_t* p) {
    return static_cast<std::uint16_t>((static_cast<std::uint16_t>(p[0]) << 8) |
                                      static_cast<std::uint16_t>(p[1]));
}

}  // namespace

std::vector<std::uint8_t> EncodeFrame(FrameType type, std::uint16_t seq_num,
                                      const std::uint8_t* payload, std::size_t len) {
    std::vector<std::uint8_t> out;
    out.reserve(kFrameHeaderSize + len);

    const auto total = static_cast<std::uint32_t>(kFrameHeaderSize + len);
    out.push_back(static_cast<std::uint8_t>((total >> 24) & 0xFF));
    out.push_back(static_cast<std::uint8_t>((total >> 16) & 0xFF));
    out.push_back(static_cast<std::uint8_t>((total >> 8) & 0xFF));
    out.push_back(static_cast<std::uint8_t>(total & 0xFF));

    PutU16(out, static_cast<std::uint16_t>(type));
    PutU16(out, seq_num);

    // CRC 覆盖 Type|SeqNum|Payload（不含 Length，不含 CRC 自身）。
    // 增量累加，不留临时缓冲 —— 边界线程上也不该有多余分配。
    std::uint16_t crc = Crc16Update(kCrc16Init, out.data() + 4, kFrameHeaderTail - 2);
    crc = Crc16Update(crc, payload, len);
    PutU16(out, crc);

    if (len > 0) {
        out.insert(out.end(), payload, payload + len);
    }
    return out;
}

Result<Frame> DecodeFrame(const std::uint8_t* buf, std::size_t len) {
    if (buf == nullptr || len < kFrameHeaderTail) {
        return Result<Frame>::Err(ErrorCode::EInvalidRequest);
    }
    Frame frame;
    frame.type = static_cast<FrameType>(GetU16(buf));
    frame.seq_num = GetU16(buf + 2);
    const std::uint16_t wire_crc = GetU16(buf + 4);

    const std::size_t payload_len = len - kFrameHeaderTail;
    if (payload_len > kMaxPayload) {
        return Result<Frame>::Err(ErrorCode::EInvalidRequest);
    }

    // 覆盖两段：Type|SeqNum 与 Payload。CRC 字段夹在中间，不能取一段连续区间。
    std::uint16_t actual_crc = Crc16Update(kCrc16Init, buf, kFrameHeaderTail - 2);
    actual_crc = Crc16Update(actual_crc, buf + kFrameHeaderTail, payload_len);
    if (actual_crc != wire_crc) {
        return Result<Frame>::Err(ErrorCode::EVerifyFailed);
    }

    frame.payload.assign(buf + kFrameHeaderTail, buf + len);
    return Result<Frame>::Ok(std::move(frame));
}

}  // namespace km3
