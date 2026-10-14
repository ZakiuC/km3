import { defineStore } from 'pinia'
import { ref } from 'vue'
import { authApi } from '@/api/auth'
import { clearCsrfToken, setCsrfToken } from '@/api/client'
import { can, type Capability } from '@/permissions'
import type { SessionUser } from '@/api/types'

export const useSessionStore = defineStore('session', () => {
  const user = ref<SessionUser | null>(null)
  const restored = ref(false)
  let inflight: Promise<void> | null = null

  /**
   * 启动时恢复会话。票据在 HttpOnly Cookie 里，脚本读不到，
   * 唯一能确认"我还登录着吗"的方式是问服务端。
   * 幂等：路由守卫与启动流程都会调，重复调用只发一次请求。
   */
  function restore(): Promise<void> {
    if (restored.value) return Promise.resolve()
    if (inflight) return inflight
    inflight = (async () => {
      try {
        const res = await authApi.refresh()
        setCsrfToken(res.csrf_token)
        user.value = res.user
      } catch {
        // 未登录是**正常状态**，不是错误：直接进登录页即可，不弹错误提示。
        user.value = null
      } finally {
        restored.value = true
        inflight = null
      }
    })()
    return inflight
  }

  async function login(username: string, password: string): Promise<void> {
    const res = await authApi.login(username, password)
    setCsrfToken(res.csrf_token)
    user.value = res.user
    restored.value = true
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } catch {
      // 服务端可能已吊销或网络已断。本地照样清干净，
      // 否则用户会卡在一个"看起来还登录着但什么都做不了"的状态。
    }
    clearCsrfToken()
    user.value = null
  }

  function has(capability: Capability): boolean {
    return user.value ? can(user.value.role, capability) : false
  }

  return { user, restored, restore, login, logout, has }
})
