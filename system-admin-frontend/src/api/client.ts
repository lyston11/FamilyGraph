/**
 * 独立 admin axios client：baseURL 固定 /admin-api，只与 8002 admin listener
 * 通信。绝不访问家庭 /api，也不使用家庭 fg.refresh_token 等存储。
 *
 * - request：由 stores 注入的 provider 提供 Bearer access token（仅内存）与
 *   敏感详情票据（X-Admin-Access-Session，仅内存）；
 * - response：把后端统一错误外壳 {"error":{code,message,detail}} 映射为
 *   AdminApiError；401 ADMIN_UNAUTHORIZED 触发一次 refresh+重试（由 auth
 *   store 注入的 handler 决定），刷新失败即会话过期回调（清会话回登录页）。
 */

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ADMIN_ERROR_CODES, type ApiErrorBody, type AccessTargetType } from '@/types/api'
import { expectObject, expectString } from '@/api/decode'

export const SESSION_EXPIRED_CODE = 'ADMIN_SESSION_EXPIRED'

export class AdminApiError extends Error {
  readonly status: number
  readonly code: string
  readonly serverMessage: string
  /** 后端错误外壳 detail（如 allowed_models 白名单载荷）；网络/未知错误为 undefined。 */
  readonly detail?: unknown

  constructor(status: number, code: string, serverMessage: string, detail?: unknown) {
    super(serverMessage || `请求失败（${status}）`)
    this.name = 'AdminApiError'
    this.status = status
    this.code = code
    this.serverMessage = serverMessage
    this.detail = detail
  }
}

/** 敏感详情票据失效（无/错目标/过期/撤销/跨管理员统一 403）。 */
export class AccessSessionInvalidError extends AdminApiError {
  constructor() {
    super(403, ADMIN_ERROR_CODES.ACCESS_SESSION_INVALID, '访问会话无效或已过期')
    this.name = 'AccessSessionInvalidError'
  }
}

/** 敏感请求缺票据（fail-closed：绝不无票据打敏感端点）。 */
export class AccessSessionRequiredError extends AdminApiError {
  constructor() {
    super(0, ADMIN_ERROR_CODES.ACCESS_SESSION_INVALID, '缺少访问会话票据')
    this.name = 'AccessSessionRequiredError'
  }
}

/** 请求被调用方中止（轮询清理/组件卸载），不是业务错误。 */
export class RequestAbortedError extends Error {
  constructor() {
    super('request aborted')
    this.name = 'RequestAbortedError'
  }
}

export interface AdminClientWiring {
  /** 当前内存中的 admin access token（未登录为 null）。 */
  getAccessToken: () => string | null
  /** 当前目标绑定且未过期的敏感票据（无则 null）。 */
  getAccessSessionToken: (targetType: AccessTargetType, targetId: number) => string | null
  /** 401 时尝试 refresh；返回 true 表示已续期可重试一次。 */
  tryRefresh: () => Promise<boolean>
  /** refresh 也失败：会话已过期（store 已清空，回调负责跳登录页）。 */
  onSessionExpired: () => void
}

interface AdminRequestConfig extends InternalAxiosRequestConfig {
  /** 敏感详情请求标记：拦截器据此附加 X-Admin-Access-Session。 */
  adminAccessTarget?: { targetType: AccessTargetType; targetId: number }
  /** 防止 401 refresh 重试无限循环。 */
  _adminRetried?: boolean
}

let wiring: AdminClientWiring | null = null

export function wireAdminApiClient(next: AdminClientWiring): void {
  wiring = next
}

export function getAdminClientWiring(): AdminClientWiring | null {
  return wiring
}

function extractErrorBody(data: unknown): ApiErrorBody['error'] | null {
  if (typeof data !== 'object' || data === null || !('error' in data)) return null
  const error = expectObject((data as Record<string, unknown>)['error'], 'error')
  return {
    code: expectString(error['code'], 'error.code'),
    message: expectString(error['message'], 'error.message'),
    detail: error['detail'],
  }
}

function toAdminApiError(error: unknown): AdminApiError | RequestAbortedError {
  if (error instanceof AdminApiError) return error
  if (error instanceof RequestAbortedError) return error
  if (axios.isCancel(error)) return new RequestAbortedError()
  if (error instanceof AxiosError && error.response) {
    const parsed = extractErrorBody(error.response.data)
    if (parsed) {
      return new AdminApiError(error.response.status, parsed.code, parsed.message, parsed.detail)
    }
    return new AdminApiError(error.response.status, 'HTTP_ERROR', error.message)
  }
  if (error instanceof Error) {
    return new AdminApiError(0, 'NETWORK_ERROR', '网络连接失败，请稍后重试')
  }
  return new AdminApiError(0, 'NETWORK_ERROR', '网络连接失败，请稍后重试')
}

export const adminApiClient: AxiosInstance = axios.create({
  baseURL: '/admin-api',
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

adminApiClient.interceptors.request.use((config) => {
  const adminConfig = config as AdminRequestConfig
  if (wiring) {
    const token = wiring.getAccessToken()
    if (token) {
      adminConfig.headers.set('Authorization', `Bearer ${token}`)
    }
    const target = adminConfig.adminAccessTarget
    if (target) {
      const ticket = wiring.getAccessSessionToken(target.targetType, target.targetId)
      // fail-closed：无票据直接拒绝，绝不无票据请求敏感端点
      if (!ticket) {
        throw new AccessSessionRequiredError()
      }
      adminConfig.headers.set('X-Admin-Access-Session', ticket)
    }
  }
  return adminConfig
})

adminApiClient.interceptors.response.use(
  (response: AxiosResponse) => response,
  async (error: unknown) => {
    const axiosError = error instanceof AxiosError ? error : null
    if (axiosError && axiosError.response) {
      const parsed = extractErrorBody(axiosError.response.data)
      const status = axiosError.response.status
      const config = axiosError.config as AdminRequestConfig | undefined
      const isAuthRequest = Boolean(config?.url?.startsWith('/auth/'))

      // 会话失效统一 401 ADMIN_UNAUTHORIZED（登录失败是 ADMIN_INVALID_CREDENTIALS，
      // 不进入 refresh 流程）。票据失效是 403，由调用方处理重新授权。
      if (
        status === 401 &&
        parsed?.code === ADMIN_ERROR_CODES.UNAUTHORIZED &&
        wiring &&
        config &&
        !isAuthRequest &&
        !config._adminRetried
      ) {
        const refreshed = await wiring.tryRefresh()
        if (refreshed) {
          config._adminRetried = true
          const token = wiring.getAccessToken()
          if (token) {
            config.headers.set('Authorization', `Bearer ${token}`)
          }
          return adminApiClient.request(config)
        }
        wiring.onSessionExpired()
        return Promise.reject(new AdminApiError(401, SESSION_EXPIRED_CODE, '会话已过期，请重新登录'))
      }

      if (status === 403 && parsed?.code === ADMIN_ERROR_CODES.ACCESS_SESSION_INVALID) {
        return Promise.reject(new AccessSessionInvalidError())
      }
      if (parsed) {
        return Promise.reject(new AdminApiError(status, parsed.code, parsed.message, parsed.detail))
      }
    }
    return Promise.reject(toAdminApiError(error))
  },
)

/** 统一请求入口：所有错误都归一为 AdminApiError（中止除外）。 */
export async function adminRequest<T>(config: {
  method: 'get' | 'post' | 'put' | 'patch'
  url: string
  data?: unknown
  params?: Record<string, string | number | boolean | null | undefined>
  accessTarget?: { targetType: AccessTargetType; targetId: number }
  responseType?: 'json' | 'blob'
  signal?: AbortSignal
}): Promise<T> {
  try {
    const response = await adminApiClient.request<T>({
      method: config.method,
      url: config.url,
      data: config.data,
      params: config.params,
      responseType: config.responseType ?? 'json',
      signal: config.signal,
      ...(config.accessTarget ? { adminAccessTarget: config.accessTarget } : {}),
    } as AdminRequestConfig)
    return response.data
  } catch (error) {
    throw toAdminApiError(error)
  }
}
