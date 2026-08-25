import axios, { AxiosError, type AxiosRequestConfig } from 'axios'

import { ApiError, toApiError } from '@/api/errors'
import type { TokenPairResponse } from '@/types/api'

/**
 * 统一 API 客户端。
 *
 * 生产环境经 nginx 反代 /api → api:8000；开发环境经 vite proxy 同路径转发，
 * 因此 baseURL 固定为相对路径 /api，两条链路行为一致（m0a design）。
 */
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 15_000,
})

// 裸实例：refresh 自身不再经过拦截器，避免循环刷新
export const rawClient = axios.create({ baseURL: '/api', timeout: 15_000 })

/** access token 存内存态（Pinia），由 auth store 启动时注册读取器 */
let tokenReader: () => string | null = () => null
let refreshExecutor: (() => Promise<TokenPairResponse>) | null = null
let sessionExpiredHandler: (() => void) | null = null

export function registerTokenReader(reader: () => string | null): void {
  tokenReader = reader
}

export function registerRefreshExecutor(executor: () => Promise<TokenPairResponse>): void {
  refreshExecutor = executor
}

export function registerSessionExpiredHandler(handler: () => void): void {
  sessionExpiredHandler = handler
}

apiClient.interceptors.request.use((config) => {
  const accessToken = tokenReader()
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`
  }
  return config
})

// 401 静默刷新：单飞行（single-flight），并发请求共享同一次刷新
let refreshInFlight: Promise<void> | null = null

function isAuthEndpoint(url: string | undefined): boolean {
  return !!url && url.startsWith('/auth/')
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    const axiosError = error as AxiosError
    const original = axiosError.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
    const status = axiosError.response?.status

    if (
      status === 401 &&
      original &&
      !original._retried &&
      !isAuthEndpoint(original.url) &&
      refreshExecutor
    ) {
      if (!refreshInFlight) {
        refreshInFlight = refreshExecutor()
          .then(() => undefined)
          .finally(() => {
            refreshInFlight = null
          })
      }
      try {
        await refreshInFlight
        original._retried = true
        return await apiClient.request(original)
      } catch {
        // 刷新失败：会话彻底失效，交由 auth store 清理并跳登录页
        sessionExpiredHandler?.()
        throw toApiError(axiosError)
      }
    }

    if (status === 401 && isAuthEndpoint(original?.url)) {
      // /auth/ 端点自身的 401（如 refresh 失效）：会话不可恢复
      sessionExpiredHandler?.()
    }

    throw toApiError(error)
  },
)

export { ApiError }
