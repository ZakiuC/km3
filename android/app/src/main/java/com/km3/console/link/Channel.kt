package com.km3.console.link

/**
 * 两条通道与降级关系。见 docs/架构设计/前端程序架构.md §2.1。
 *
 * 关键约束：两条通道的命令语义必须完全一致（等待结果 / 超时 / 失败）。
 * 不得在中转通道上硬编码返回成功——降级态下操作员会看到"成功"但机器人没动。
 */
enum class Channel { DIRECT, RELAY, OFFLINE }

/** 命令在途状态。"结果未知"必须能表达并展示给用户，不得静默丢弃。 */
sealed interface CommandState {
    data object Pending : CommandState
    data class Acked(val commandId: String) : CommandState
    data class Completed(val commandId: String, val success: Boolean) : CommandState
    data class Failed(val commandId: String, val errorCode: String?) : CommandState
    /** 通道切换导致结果不可知：必须提示用户确认设备状态。 */
    data class Unknown(val commandId: String, val reason: String) : CommandState
}
