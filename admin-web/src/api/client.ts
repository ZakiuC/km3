// 与服务器通信的**唯一**出口。
//
// 规则（管理端Web程序架构.md §三）：只有本目录允许出现 URL 与请求逻辑。
// 组件与 store 一律经此调用，这样接口变更只有一个落点。
//
// 为什么不用 axios：这里需要的四件事——解包统一响应、附加 Cookie、
// 把错误码转成异常、支持取消——加起来不到百行，引一个请求库
// 只多一份供应链风险，收益不足。

import type { ApiEnvelope, ErrorCode } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

/** 携带错误码的异常。页面据此分支，而不是解析错误字符串。 */
export class ApiError extends Error {
  readonly code: ErrorCode | string
  readonly httpStatus: number

  constructor(code: ErrorCode | string, message: string, httpStatus: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.httpStatus = httpStatus
  }
}

/**
 * CSRF 双提交令牌。
 *
 * SameSite=Strict 是**必要但不充分**的防护：它依赖浏览器行为，
 * 且不覆盖同站子域场景。因此非幂等方法必须回填此头，
 * 由服务端比对（管理端Web程序架构.md §4.2）。
 *
 * 令牌本身可由脚本读取——它只用于证明"请求来自本页面的脚本"，
 * 真正的凭据是那个 HttpOnly 的会话 Cookie，脚本读不到。
 */
let csrfToken = ''
export function setCsrfToken(token: string): void {
  csrfToken = token
}
export function clearCsrfToken(): void {
  csrfToken = ''
}

/**
 * 查询参数。显式的索引签名让各接口的具名查询类型（如 LogQuery）
 * 可以直接传入，而不必在调用点手工展开成匿名对象。
 */
export type QueryParams = Record<string, string | number | boolean | undefined>

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH'
  body?: unknown
  query?: QueryParams
  signal?: AbortSignal
}

const MUTATING = new Set(['POST', 'PUT', 'DELETE', 'PATCH'])

function buildUrl(path: string, query?: QueryParams): string {
  const url = `${BASE}/api/v1${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined) params.append(k, String(v))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }

  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (MUTATING.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken

  // tsconfig 开了 exactOptionalPropertyTypes：把 undefined 赋给可选属性是**类型错误**，
  // 而 RequestInit 的 body/signal 都是可选的。因此逐项按需赋值，不写 `x: undefined`。
  const init: RequestInit = {
    method,
    headers,
    // 凭据必须带上：会话票据是 HttpOnly Cookie，脚本读不到也无需读到。
    credentials: 'same-origin',
  }
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body)
  if (opts.signal !== undefined) init.signal = opts.signal

  const res = await fetch(buildUrl(path, opts.query), init)

  // 401 统一视为会话失效。页面不做各自的"我是不是掉线了"判断。
  if (res.status === 401) {
    throw new ApiError('EUNAUTHORIZED', '会话已失效，请重新登录', 401)
  }

  let envelope: ApiEnvelope<T>
  try {
    envelope = (await res.json()) as ApiEnvelope<T>
  } catch {
    // 服务端返回了非 JSON（网关错误页等）。给出可读信息而不是让 JSON 解析错误冒到页面上。
    throw new ApiError('EINTERNAL', `响应不是合法 JSON（HTTP ${res.status}）`, res.status)
  }

  if (!res.ok || !envelope.success) {
    throw new ApiError(
      envelope.error?.code ?? 'EINTERNAL',
      envelope.error?.message ?? `请求失败（HTTP ${res.status}）`,
      res.status,
    )
  }

  return envelope.data as T
}

// 同样因为 exactOptionalPropertyTypes，这里不能用 `{ method: 'GET', query, signal }`：
// 那是把可能为 undefined 的值显式赋给了可选属性。
function withOptional(base: RequestOptions, key: 'query' | 'body' | 'signal',
                      value: unknown): RequestOptions {
  if (value !== undefined) {
    // 单一收窄点。调用方保证 key 与 value 类型一致，比在三处各写一遍 if 更难写错。
    ;(base as Record<string, unknown>)[key] = value
  }
  return base
}

export const http = {
  get: <T>(path: string, query?: QueryParams, signal?: AbortSignal) =>
    request<T>(path, withOptional(withOptional({ method: 'GET' }, 'query', query),
                                  'signal', signal)),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, withOptional({ method: 'POST' }, 'body', body)),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, withOptional({ method: 'PUT' }, 'body', body)),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
