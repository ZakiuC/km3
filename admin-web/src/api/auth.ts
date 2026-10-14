import { http } from './client'
import type { SessionUser } from './types'

export interface SessionResult {
  user: SessionUser
  /**
   * 可读的 CSRF 令牌（双提交用，见 client.ts）。
   * 会话票据本身在 HttpOnly Cookie 中，脚本读不到也不需要读到。
   */
  csrf_token: string
}

export const authApi = {
  login: (username: string, password: string) =>
    http.post<SessionResult>('/auth/login', { username, password }),
  logout: () => http.post<null>('/auth/logout'),
  /**
   * 会话恢复用 `/auth/refresh`（服务器端程序架构.md §3.1 已有该端点）。
   * 刻意不新增 `/auth/me`：刷新页面需要的正是"续期 + 拿回当前用户"，
   * 这与 refresh 的语义重合，多一个端点只会多一处需要保持一致的状态。
   */
  refresh: () => http.post<SessionResult>('/auth/refresh'),
}
