package com.km3.console.link

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * 路径 A 帧编解码的跨实现一致性测试。
 *
 * 黄金向量与设备端 device/tests/test_frame.cpp 的 golden_frames_are_stable
 * 使用同一组输入。**两端必须逐字节相同**：若本测试通过而 C++ 侧失败（或反之），
 * 说明发生了协议漂移，必须修到一致为止。
 *
 * 权威定义：docs/架构设计/通信协议规范.md §2.1
 *
 * 注意本测试在未安装 JDK 17 / Android SDK 时无法运行，见 android/README.md。
 */
class FrameCodecTest {

    private fun hex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02X".format(it.toInt() and 0xFF) }

    @Test
    fun `crc16 matches the standard check value`() {
        // CRC-16/CCITT-FALSE 公开检查值，用于确认算法本身没写错。
        assertEquals(0x29B1, Crc16.of("123456789".toByteArray(Charsets.US_ASCII)))
        assertEquals(0xFFFF, Crc16.of(ByteArray(0)))
    }

    @Test
    fun `golden heartbeat frame is byte identical to the device`() {
        val f = FrameCodec.encode(FrameType.HEARTBEAT, 0x0001, ByteArray(0))
        assertEquals("0000000A00010001A3D1", hex(f))
    }

    @Test
    fun `golden estop frame is byte identical to the device`() {
        val f = FrameCodec.encode(FrameType.ESTOP, 0x0002, ByteArray(0))
        assertEquals("0000000A010300028B66", hex(f))
    }

    @Test
    fun `golden command frame is byte identical to the device`() {
        val payload = byteArrayOf(0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte())
        val f = FrameCodec.encode(FrameType.COMMAND, 0x0003, payload)
        // 完整黄金向量，与 C++ 侧同一组
        assertEquals("0000000D0100000381D1DEADBE", hex(f))
        assertEquals(13, f.size)
        assertEquals(0x0D, f[3].toInt() and 0xFF)   // 大端长度
        assertEquals(0x01, f[4].toInt() and 0xFF)   // 大端类型
        assertEquals(0x03, f[7].toInt() and 0xFF)   // 大端序号
    }

    @Test
    fun `length prefix is big endian for a 300 byte payload`() {
        val payload = ByteArray(300) { 0x5A }
        val f = FrameCodec.encode(FrameType.SENSOR_DATA, 0x0100, payload)
        assertEquals(310, f.size)
        assertEquals(0x00, f[0].toInt() and 0xFF)
        assertEquals(0x00, f[1].toInt() and 0xFF)
        assertEquals(0x01, f[2].toInt() and 0xFF)   // 310 = 0x00000136
        assertEquals(0x36, f[3].toInt() and 0xFF)
    }

    @Test
    fun `round trip preserves every field`() {
        val payload = byteArrayOf(1, 2, 3, 4, 5)
        val wire = FrameCodec.encode(FrameType.COMMAND_RESULT, 0xBEEF, payload)
        // 完整性校验：与 C++ 侧同一向量
        assertEquals("0000000F0102BEEF70380102030405", hex(wire))

        val frame = FrameCodec.read(wire.inputStream())
            ?: fail("decode returned null") as Nothing

        assertEquals(FrameType.COMMAND_RESULT, frame.type)
        assertEquals(0xBEEF, frame.seqNum)
        assertArrayEquals(payload, frame.payload)
    }

    @Test
    fun `corrupted payload is rejected by crc`() {
        val wire = FrameCodec.encode(FrameType.COMMAND, 0x0010, byteArrayOf(0xAA.toByte(), 0xBB.toByte()))
        wire[wire.size - 1] = (wire[wire.size - 1].toInt() xor 0xFF).toByte()
        try {
            FrameCodec.read(wire.inputStream())
            fail("篡改后的帧必须被 CRC 拒绝")
        } catch (e: IllegalStateException) {
            assertTrue("错误信息应指出 CRC：${e.message}", e.message!!.contains("CRC"))
        }
    }

    @Test
    fun `out of range length is rejected`() {
        // Length = 0xFFFFFFFF，必须拒绝而不是尝试分配 4GB
        val bad = byteArrayOf(0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte())
        try {
            FrameCodec.read(bad.inputStream())
            fail("越界长度必须被拒绝")
        } catch (e: IllegalStateException) {
            assertTrue("错误信息应指出越界：${e.message}", e.message!!.contains("out of range"))
        }
    }
}
