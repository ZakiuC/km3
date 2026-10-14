import { onBeforeUnmount, ref, shallowRef } from 'vue'
import type { LogEntry } from '@/api/types'

/**
 * 日志实时流（管-F04）。端点 `/api/v1/frontend/ws`，
 * 握手时校验会话票据，建立后不重复认证（服务器端程序架构.md §3.2）。
 *
 * 浏览器 WebSocket 无法自定义请求头，但同源握手会自动带上 Cookie，
 * 这正是选用 HttpOnly Cookie 而非 localStorage 的额外收益：
 * 实时通道不需要另想办法传票据。
 */
export function useLogStream(topic: string) {
  const entries = shallowRef<LogEntry[]>([])
  const connected = ref(false)
  const error = ref<string | null>(null)
  const MAX_BUFFER = 2000
  let ws: WebSocket | null = null

  function connect(): void {
    error.value = null
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${proto}//${location.host}/api/v1/frontend/ws`)

    ws.onopen = () => {
      connected.value = true
      // 服务器会校验该设备是否属于当前用户可管范围，不能只验"已登录"。
      ws?.send(JSON.stringify({ type: 'subscribe', topic }))
    }

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data as string) as { type: string; payload?: LogEntry }
        if (msg.type === 'data' && msg.payload) {
          // 有界缓冲：日志流是无限的，UI 留住全部条目只会把标签页拖死。
          const next = [msg.payload, ...entries.value]
          entries.value = next.length > MAX_BUFFER ? next.slice(0, MAX_BUFFER) : next
        }
      } catch {
        // 单条消息解析失败不应中断整个流。
      }
    }

    ws.onclose = () => {
      connected.value = false
    }
    ws.onerror = () => {
      error.value = '实时日志连接失败'
    }
  }

  function close(): void {
    ws?.close()
    ws = null
    connected.value = false
  }

  onBeforeUnmount(close)

  return { entries, connected, error, connect, close }
}
