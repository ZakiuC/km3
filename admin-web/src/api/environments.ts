import { http } from './client'
import type { Environment } from './types'

/**
 * 环境列表。
 *
 * **这是管理端提出的新接口需求**：`服务器端程序架构.md` §3.1 当前列出的端点里
 * 没有获取环境的接口，而 §2.1 的 `environments` 表与 `devices.env_id` 都要求
 * 演示/生产隔离。没有这个端点，管理端的环境切换器无法填充，
 * 只能在代码里硬编码环境列表——那与"环境由数据库定义"直接矛盾。
 *
 * 已同步到服务器端文档 §3.1。
 */
export const environmentsApi = {
  list: () => http.get<Environment[]>('/environments'),
}
