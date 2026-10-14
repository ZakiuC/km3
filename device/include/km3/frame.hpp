#pragma once
// 路径 A 的帧编解码：Length(4) | Type(2) | SeqNum(2) | CRC16(2) | Payload(N)。
// 见 docs/架构设计/通信协议规范.md §2.1 —— 这是唯一权威帧定义。
//
// 对端是 Android（Kotlin）。两端必须逐字节一致，跨实现一致性由共享测试向量保证：
// 见 tests/test_frame.cpp 的 kGoldenFrames，Android 侧 FrameCodecTest 引用同一组值。
//
// 注意：本文件不属于实时路径。RT 线程只写环形缓冲，编解码由边界线程完成。

#include <cstddef>
#include <cstdint>
#include <vector>

#include "km3/error_code.hpp"
#include "km3/result.hpp"

namespace km3 {

// CRC-16/CCITT-FALSE（poly 0x1021，init 0xFFFF，无反射，无最终异或）。
// 标准检查值：crc16("123456789") == 0x29B1，crc16("") == 0xFFFF。
//
// 为什么需要增量接口：帧的 CRC 覆盖 Type|SeqNum|Payload，而这三段在字节流里
// **并不连续** —— CRC 字段本身夹在 SeqNum 与 Payload 之间。因此必须能分段累加。
// 早期实现用 Crc16(buf, len-2) 取一段连续区间，结果把 CRC 字段算了进去、
// 又漏掉了载荷的最后一个字节，使编码端与解码端 CRC 覆盖不一致。
// 该缺陷由帧往返测试捕获（见 tests/test_frame.cpp）。
constexpr std::uint16_t kCrc16Init = 0xFFFF;

std::uint16_t Crc16Update(std::uint16_t crc, const std::uint8_t* data, std::size_t len);

inline std::uint16_t Crc16(const std::uint8_t* data, std::size_t len) {
    return Crc16Update(kCrc16Init, data, len);
}

enum class FrameType : std::uint16_t {
    // proto3 要求枚举首值为零。协议规范未定义该取值，收到即视为协议错误。
    Unknown       = 0x0000,
    Heartbeat     = 0x0001,
    Command       = 0x0100,
    CommandAck    = 0x0101,
    CommandResult = 0x0102,
    Estop         = 0x0103,
    StatusReport  = 0x0200,
    SensorData    = 0x0300,
    ConfigGet     = 0x0500,
    ConfigSet     = 0x0501,
    ConfigResponse= 0x0502,
    OtaOffer      = 0x0600,
    OtaProgress   = 0x0601,
    OtaReport     = 0x0602,
    OtaRollback   = 0x0603,
    TunnelOpen    = 0x0700,
    TunnelData    = 0x0701,
    TunnelClose   = 0x0702,
    LogBatch      = 0x0800,
    Error         = 0xFFFF,
};

// 头部除 Length 外的固定长度（Type + SeqNum + CRC16）
inline constexpr std::size_t kFrameHeaderTail = 6;
// 整个头部长度
inline constexpr std::size_t kFrameHeaderSize = 10;
// 单帧载荷上限，防止畸形长度字导致巨额分配
inline constexpr std::size_t kMaxPayload = 8u * 1024u * 1024u;

struct Frame {
    FrameType type = FrameType::Heartbeat;
    std::uint16_t seq_num = 0;
    std::vector<std::uint8_t> payload;
};

// 编码为完整字节流（含长度前缀）。crc 覆盖 Type|SeqNum|Payload，与 Android 一致。
std::vector<std::uint8_t> EncodeFrame(FrameType type, std::uint16_t seq_num,
                                      const std::uint8_t* payload, std::size_t len);

inline std::vector<std::uint8_t> EncodeFrame(const Frame& frame) {
    return EncodeFrame(frame.type, frame.seq_num, frame.payload.data(), frame.payload.size());
}

// 从字节流解析一帧。buf 指向紧跟 Length 之后的完整头+载荷（长度由外层读入）。
// 返回错误码：EInvalidRequest（长度越界）、EVerifyFailed（CRC 不符）。
Result<Frame> DecodeFrame(const std::uint8_t* buf, std::size_t len);

}  // namespace km3
