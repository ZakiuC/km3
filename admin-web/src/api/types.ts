// 与服务端契约一一对应的类型。设计权威：docs/架构设计/服务器端程序架构.md §3.1。
//
// 为什么手写而不是从 OpenAPI 生成：服务端目前没有 OpenAPI 描述文件。
// 手写的不变量是"**编译期能发现契约漂移**"——字段改名、类型变化会在
// vue-tsc 阶段报错，而不是等到运行时页面空白。
// 服务端产出规范描述后，此文件替换为生成产物，其余代码不改。

/** 统一响应包。见 服务器端程序架构.md §3.1。 */
export interface ApiEnvelope<T> {
  success: boolean
  data?: T
  error?: { code: string; message: string }
  ts: string
}

/** 分页列表响应。列表接口统一支持 ?page=&size=&sort=&order=。 */
export interface Paged<T> {
  items: T[]
  page: number
  size: number
  total: number
}

/** 协议规范 §六 错误码。此处只列前端会做分支处理的那些。 */
export type ErrorCode =
  | 'EINTERNAL'
  | 'EINVALID_REQUEST'
  | 'EUNAUTHORIZED'
  | 'EPERMISSION'
  | 'ENOT_FOUND'
  | 'ERATE_LIMIT'
  | 'EDEVICE_OFFLINE'
  | 'EDEVICE_BUSY'
  | 'EINVALID_CONFIG'
  | 'ECONFIG_CONFLICT'

export type Role = 'superadmin' | 'admin' | 'operator' | 'viewer'

/** 环境。演示与生产必须隔离，见 服务器端程序架构.md §2.1。 */
export interface Environment {
  id: number
  code: 'demo' | 'prod'
  name: string
}

export interface SessionUser {
  id: number
  username: string
  email: string
  role: Role
}

export interface Device {
  id: number
  device_id: string
  name: string
  model: string
  env_id: number
  firmware_version: string | null
  status: 'online' | 'offline' | 'error'
  last_seen_at: string | null
  location: string | null
}

/** 管-F03 传感器历史。schema_version 是硬约束，跨版本回滚时旧固件会读到新固件写的数据。 */
export interface SensorPoint {
  ts: string
  value_num: number | null
  value_json: Record<string, unknown> | null
  quality: number
  schema_version: number
}

/** 管-F04 日志。fields 为设备侧自由字段。 */
export interface LogEntry {
  ts: string
  level: number
  module: string
  message: string
  fields?: Record<string, unknown>
  trace_id?: string
}

/** 管-F06 固件与部署。 */
export interface Firmware {
  id: number
  version: string
  model: string
  env_id: number
  file_size: number
  file_hash: string
  changelog: string | null
  is_stable: boolean
  created_at: string
}

export interface OtaDeployment {
  id: number
  firmware_id: number
  device_id: string
  status: 'pending' | 'downloading' | 'installing' | 'completed' | 'failed' | 'rolled_back'
  progress: number
  prev_version: string | null
  kind: 'full' | 'incremental'
}
