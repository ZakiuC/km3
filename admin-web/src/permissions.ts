// 权限矩阵。**与服务端 permission.hpp 一一对应**（服务器端程序架构.md §4.4）。
//
// 为什么是数据而不是散落的 if：矩阵一旦分散到各页面，
// 第一个人改了服务端矩阵时，前端会悄悄失真，且没有任何编译期或测试期信号。
// 集中成一张表后，至少有一个地方能一眼看完，并可以被单元测试锁住。
//
// 前端这份的用途是**隐藏入口**，不是安全边界：服务端仍逐请求校验。

import type { Role } from './api/types'

export const CAPABILITIES = {
  view_device: ['superadmin', 'admin', 'operator', 'viewer'],
  view_logs: ['superadmin', 'admin', 'operator', 'viewer'],
  send_command: ['superadmin', 'admin', 'operator'],
  modify_config: ['superadmin', 'admin', 'operator'],
  upload_firmware: ['superadmin', 'admin'],
  manage_users: ['superadmin', 'admin'],
  view_audit: ['superadmin', 'admin'],
  switch_environment: ['superadmin'],
} as const

export type Capability = keyof typeof CAPABILITIES

export function can(role: Role, capability: Capability): boolean {
  const allowed = CAPABILITIES[capability] as readonly Role[]
  return allowed.includes(role)
}
