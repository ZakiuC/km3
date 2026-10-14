package com.km3.console.link

import java.io.InputStream
import java.nio.ByteBuffer

/**
 * 路径 A 的帧编解码：Length(4) | Type(2) | SeqNum(2) | CRC16(2) | Payload(N)。
 * 见 docs/架构设计/通信协议规范.md §2.1。这是唯一权威帧定义。
 *
 * Payload 为 Protobuf（protobuf-javalite 生成），不是 JSON。
 *
 * **字节序**：长度、类型、序号、CRC 一律**大端**（网络字节序）。
 * 两端一致性由 FrameCodecTest 的黄金向量保证，向量与设备端 test_frame.cpp 共用。
 */
object FrameCodec {
    /** 头部中 Length 之外的固定长度（Type + SeqNum + CRC16） */
    const val HEADER_TAIL = 6

    /** 完整头部长度 */
    const val HEADER_SIZE = 10

    /** 单帧载荷上限，防止畸形长度字导致巨额分配 */
    const val MAX_PAYLOAD = 8 * 1024 * 1024

    data class Frame(val type: Int, val seqNum: Int, val payload: ByteArray) {
        override fun equals(other: Any?): Boolean =
            other is Frame && type == other.type && seqNum == other.seqNum &&
                payload.contentEquals(other.payload)

        override fun hashCode(): Int =
            (type * 31 + seqNum) * 31 + payload.contentHashCode()
    }

    fun encode(type: Int, seqNum: Int, payload: ByteArray): ByteArray {
        val total = HEADER_SIZE + payload.size
        val buf = ByteBuffer.allocate(total)
        buf.putInt(total)                 // 大端长度
        buf.putShort(type.toShort())      // 大端类型
        buf.putShort(seqNum.toShort())    // 大端序号
        buf.putShort(Crc16.forFrame(type, seqNum, payload).toShort())
        buf.put(payload)
        return buf.array()
    }

    /** 读一帧。流结束返回 null；长度越界或 CRC 不符抛 IllegalStateException。 */
    fun read(input: InputStream): Frame? {
        val lengthBytes = ByteArray(4)
        if (!readFully(input, lengthBytes)) return null
        val total = ByteBuffer.wrap(lengthBytes).int

        if (total < HEADER_SIZE || total > HEADER_SIZE + MAX_PAYLOAD) {
            throw IllegalStateException("frame length out of range: $total")
        }

        val rest = ByteArray(total - 4)
        if (!readFully(input, rest)) return null

        val bb = ByteBuffer.wrap(rest)
        val type = bb.short.toInt() and 0xFFFF
        val seqNum = bb.short.toInt() and 0xFFFF
        val crc = bb.short.toInt() and 0xFFFF
        val payload = ByteArray(total - HEADER_SIZE)
        bb.get(payload)

        val expected = Crc16.forFrame(type, seqNum, payload)
        if (expected != crc) {
            throw IllegalStateException("CRC mismatch: expected $expected got $crc")
        }
        return Frame(type, seqNum, payload)
    }

    private fun readFully(input: InputStream, dst: ByteArray): Boolean {
        var off = 0
        while (off < dst.size) {
            val n = input.read(dst, off, dst.size - off)
            if (n < 0) return false
            off += n
        }
        return true
    }
}

/**
 * CRC-16/CCITT-FALSE（poly 0x1021，init 0xFFFF，无反射，无最终异或）。
 * 标准检查值：of("123456789") == 0x29B1，of(空) == 0xFFFF。
 *
 * 与设备端 device/src/middleware/frame.cpp 的 Crc16Update 等价。
 */
object Crc16 {
    private const val POLY = 0x1021
    const val INIT = 0xFFFF

    /** 增量更新。分段累加等价于对拼接结果一次计算。 */
    fun update(crcIn: Int, data: ByteArray): Int {
        var crc = crcIn
        for (b in data) {
            crc = update(crc, b)
        }
        return crc and 0xFFFF
    }

    /** 单段计算，用于测试与简单场景。 */
    fun of(data: ByteArray): Int = update(INIT, data)

    /**
     * 帧的 CRC 输入：Type(2, 大端) | SeqNum(2, 大端) | Payload。
     *
     * 早期版本这里写成 `type.toByte()` 再 `(type shr 8)`，即**小端**，
     * 与设备端的大端实现不一致，会让所有帧的 CRC 都对不上。
     * 该缺陷由黄金向量测试保证不再复现。
     */
    fun forFrame(type: Int, seqNum: Int, payload: ByteArray): Int {
        var crc = update(INIT, byteArrayOf(((type shr 8) and 0xFF).toByte(), (type and 0xFF).toByte()))
        crc = update(crc, byteArrayOf(((seqNum shr 8) and 0xFF).toByte(), (seqNum and 0xFF).toByte()))
        return update(crc, payload)
    }

    private fun update(crcIn: Int, b: Byte): Int {
        var crc = crcIn xor ((b.toInt() and 0xFF) shl 8)
        repeat(8) {
            crc = if (crc and 0x8000 != 0) ((crc shl 1) xor POLY) and 0xFFFF else (crc shl 1) and 0xFFFF
        }
        return crc
    }
}
