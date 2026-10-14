package com.km3.console.link

/**
 * 路径 A 的帧类型。取值必须与设备端 device/proto/km3.proto 的 MessageType 一致。
 * 见 docs/架构设计/通信协议规范.md §2.2。
 */
object FrameType {
    /** proto3 的零值占位。协议规范未定义，收到即视为协议错误。 */
    const val UNKNOWN         = 0x0000
    const val HEARTBEAT       = 0x0001
    const val COMMAND         = 0x0100
    const val COMMAND_ACK     = 0x0101
    const val COMMAND_RESULT  = 0x0102
    const val ESTOP           = 0x0103
    const val STATUS_REPORT   = 0x0200
    const val SENSOR_DATA     = 0x0300
    const val CONFIG_GET      = 0x0500
    const val CONFIG_SET      = 0x0501
    const val CONFIG_RESPONSE = 0x0502
    const val OTA_OFFER       = 0x0600
    const val OTA_PROGRESS    = 0x0601
    const val OTA_REPORT      = 0x0602
    const val OTA_ROLLBACK    = 0x0603
    const val TUNNEL_OPEN     = 0x0700
    const val TUNNEL_DATA     = 0x0701
    const val TUNNEL_CLOSE    = 0x0702
    const val LOG_BATCH       = 0x0800
    const val ERROR           = 0xFFFF
}
