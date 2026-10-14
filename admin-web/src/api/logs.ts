import { http, type QueryParams } from './client'
import type { LogEntry, Paged } from './types'

/**
 * 与 QueryParams 相交而非相互独立：交叉类型自带索引签名，
 * 因此可以直接传给请求层，调用点不需要手工展开。
 */
export type LogQuery = QueryParams & {
  device_id?: string
  level_min?: number
  module?: string
  from?: string
  to?: string
  page?: number
  size?: number
}

export const logsApi = {
  /** 跨设备检索，管理端用（管-F04）。 */
  search: (query: LogQuery) => http.get<Paged<LogEntry>>('/logs', query),
  forDevice: (deviceId: string, query: LogQuery) =>
    http.get<Paged<LogEntry>>(`/devices/${deviceId}/logs`, query),
}
