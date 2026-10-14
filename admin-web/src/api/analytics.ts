import { http } from './client'
import type { Paged } from './types'

export interface Overview {
  devices_online: number
  devices_total: number
  alarms_recent: number
  ota_in_progress: number
}

/**
 * B10 工时统计 —— **口径未定，接口预留，不做实现**。
 *
 * 用户已明确指示该功能需工程后期才能确定形态。此处保留签名与占位类型，
 * 不建表、不定字段、不写聚合。理由见 管理端Web程序架构.md §七：
 * 先做一版"大概的"会产出**看起来能用但口径错误**的数据，
 * 而被引用的错误数据比没有数据更危险。
 *
 * 定口径后的落点：服务端补 user_id 维度与聚合（服务器端程序架构.md §4.9），
 * 前端把占位页换成真实图表，路由与菜单无需改动。
 */
export interface WorkHourRow {
  device_id: string
  date: string
  category: string
  // TODO: 口径待定（climb / inspect / idle / maintenance 的判定来源未确认）
  user_id?: number
}

/**
 * B11 跨环境总览 —— 同样**口径未定，接口预留，不做实现**。
 */
export interface CrossEnvironmentOverview {
  // TODO: 展示口径待定（用户确认前不臆造字段）
  environments: unknown[]
}

export const analyticsApi = {
  overview: (envId: number) => http.get<Overview>('/analytics/overview', { env_id: envId }),
  /** 口径未定，占位页不调用此方法。见 §七。 */
  workHours: (envId: number, query: { from: string; to: string; page?: number; size?: number }) =>
    http.get<Paged<WorkHourRow>>('/analytics/workhours', { env_id: envId, ...query }),
  /** 口径未定，占位页不调用此方法。见 §七。 */
  crossEnvironment: () => http.get<CrossEnvironmentOverview>('/analytics/reports'),
}
