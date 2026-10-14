// 路径 A 帧编解码验证。跨实现一致性：这里的黄金向量与前端
// android/.../FrameCodecTest.kt 使用同一组输入，两端必须逐字节相同。
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include "km3/frame.hpp"
#include "test_harness.hpp"

using km3::Crc16;
using km3::DecodeFrame;
using km3::EncodeFrame;
using km3::Frame;
using km3::FrameType;
using km3::ErrorCode;

static const char* Hex(const std::vector<std::uint8_t>& v) {
    static char buf[512];
    buf[0] = '\0';
    char tmp[4];
    for (std::size_t i = 0; i < v.size() && i < 160; ++i) {
        std::snprintf(tmp, sizeof(tmp), "%02X", v[i]);
        std::strcat(buf, tmp);
    }
    return buf;
}

// CRC-16/CCITT-FALSE 的公开检查值。用于确认算法本身没写错。
KM3_TEST(crc16_matches_standard_check_value) {
    const std::uint8_t check[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    KM3_CHECK_EQ(Crc16(check, sizeof(check)), std::uint16_t{0x29B1});
    KM3_CHECK_EQ(Crc16(nullptr, 0), std::uint16_t{0xFFFF});
}

// 黄金向量：固定输入 -> 固定字节。前端 Kotlin 侧必须得到完全相同的字节串。
KM3_TEST(golden_frames_are_stable) {
    // 1) 心跳，空载荷
    {
        auto f = EncodeFrame(FrameType::Heartbeat, 0x0001, nullptr, 0);
        KM3_CHECK_EQ(Hex(f), std::string("0000000A00010001A3D1"));
    }
    // 2) 紧急停止，空载荷，序号 2
    {
        auto f = EncodeFrame(FrameType::Estop, 0x0002, nullptr, 0);
        KM3_CHECK_EQ(Hex(f), std::string("0000000A010300028B66"));
    }
    // 3) 命令，3 字节载荷 {0xDE,0xAD,0xBE}
    {
        const std::uint8_t payload[] = {0xDE, 0xAD, 0xBE};
        auto f = EncodeFrame(FrameType::Command, 0x0003, payload, sizeof(payload));
        KM3_CHECK_EQ(f.size(), std::size_t{13});
        // 长度字段 = 13
        KM3_CHECK_EQ(f[0], std::uint8_t{0x00});
        KM3_CHECK_EQ(f[3], std::uint8_t{0x0D});
        // 大端类型与序号
        KM3_CHECK_EQ(f[4], std::uint8_t{0x01});
        KM3_CHECK_EQ(f[5], std::uint8_t{0x00});
        KM3_CHECK_EQ(f[6], std::uint8_t{0x00});
        KM3_CHECK_EQ(f[7], std::uint8_t{0x03});
        // 载荷原样在尾部
        KM3_CHECK_EQ(f[10], std::uint8_t{0xDE});
        KM3_CHECK_EQ(f[11], std::uint8_t{0xAD});
        KM3_CHECK_EQ(f[12], std::uint8_t{0xBE});
    }
    // 4) 大端长度：载荷 300 字节 -> total = 310 = 0x00000136
    {
        std::vector<std::uint8_t> payload(300, 0x5A);
        auto f = EncodeFrame(FrameType::SensorData, 0x0100, payload.data(), payload.size());
        KM3_CHECK_EQ(f.size(), std::size_t{310});
        KM3_CHECK_EQ(f[0], std::uint8_t{0x00});
        KM3_CHECK_EQ(f[1], std::uint8_t{0x00});
        KM3_CHECK_EQ(f[2], std::uint8_t{0x01});
        KM3_CHECK_EQ(f[3], std::uint8_t{0x36});
    }
}

KM3_TEST(round_trip_preserves_all_fields) {
    const std::uint8_t payload[] = {0x01, 0x02, 0x03, 0x04, 0x05};
    auto wire = EncodeFrame(FrameType::CommandResult, 0xBEEF, payload, sizeof(payload));

    // 去掉 4 字节长度前缀后交给解析器（解析器接收 Length 之后的部分）
    auto r = DecodeFrame(wire.data() + 4, wire.size() - 4);
    KM3_REQUIRE(r.ok());   // 失败即中止：后面要解引用 r.value()
    KM3_CHECK(r.value().type == FrameType::CommandResult);
    KM3_CHECK_EQ(r.value().seq_num, std::uint16_t{0xBEEF});
    KM3_CHECK_EQ(r.value().payload.size(), sizeof(payload));
    for (std::size_t i = 0; i < sizeof(payload); ++i) {
        KM3_CHECK_EQ(r.value().payload[i], payload[i]);
    }
}

KM3_TEST(corruption_is_rejected) {
    const std::uint8_t payload[] = {0xAA, 0xBB};
    auto wire = EncodeFrame(FrameType::Command, 0x0010, payload, sizeof(payload));

    // 篡改载荷（最后一个字节），CRC 必须失败
    wire.back() ^= 0xFF;
    auto bad = DecodeFrame(wire.data() + 4, wire.size() - 4);
    KM3_CHECK(!bad.ok());
    KM3_CHECK(bad.code() == ErrorCode::EVerifyFailed);

    // 篡改类型字段，CRC 同样必须失败
    auto wire2 = EncodeFrame(FrameType::Command, 0x0010, payload, sizeof(payload));
    wire2[4] ^= 0x01;
    auto bad2 = DecodeFrame(wire2.data() + 4, wire2.size() - 4);
    KM3_CHECK(!bad2.ok());
    KM3_CHECK(bad2.code() == ErrorCode::EVerifyFailed);
}

KM3_TEST(malformed_input_is_rejected) {
    // 过短：不足尾部头长
    const std::uint8_t tiny[] = {0x00, 0x01};
    auto r = DecodeFrame(tiny, sizeof(tiny));
    KM3_CHECK(!r.ok());
    KM3_CHECK(r.code() == ErrorCode::EInvalidRequest);

    // 空指针
    auto r2 = DecodeFrame(nullptr, 10);
    KM3_CHECK(!r2.ok());
    KM3_CHECK(r2.code() == ErrorCode::EInvalidRequest);
}

KM3_TEST(all_frame_types_encode) {
    const FrameType types[] = {
        FrameType::Heartbeat, FrameType::Command, FrameType::CommandAck,
        FrameType::CommandResult, FrameType::Estop, FrameType::StatusReport,
        FrameType::SensorData, FrameType::ConfigGet, FrameType::ConfigSet,
        FrameType::ConfigResponse, FrameType::OtaOffer, FrameType::OtaProgress,
        FrameType::OtaReport, FrameType::OtaRollback, FrameType::TunnelOpen,
        FrameType::TunnelData, FrameType::TunnelClose, FrameType::LogBatch,
        FrameType::Error,
    };
    for (FrameType t : types) {
        auto wire = EncodeFrame(t, 1, nullptr, 0);
        KM3_CHECK_EQ(wire.size(), km3::kFrameHeaderSize);
        auto r = DecodeFrame(wire.data() + 4, wire.size() - 4);
        KM3_CHECK(r.ok());
        KM3_CHECK(r.value().type == t);
    }
}

KM3_TEST_MAIN()
