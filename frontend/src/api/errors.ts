import { AxiosError } from 'axios'

import type { ApiErrorBody } from '@/types/api'

/** 归一化后的 API 错误：统一错误外壳的解包结果 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail?: unknown

  constructor(status: number, code: string, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

function isApiErrorBody(data: unknown): data is ApiErrorBody {
  if (typeof data !== 'object' || data === null) return false
  const candidate = data as Record<string, unknown>
  if (typeof candidate.error !== 'object' || candidate.error === null) return false
  const error = candidate.error as Record<string, unknown>
  return typeof error.code === 'string' && typeof error.message === 'string'
}

export function toApiError(axiosError: unknown): ApiError {
  if (axiosError instanceof AxiosError && axiosError.response) {
    const { status, data } = axiosError.response
    if (isApiErrorBody(data)) {
      return new ApiError(status, data.error.code, data.error.message, data.error.detail)
    }
    // 非统一结构（如代理层错误）：不泄露内部细节，仅保留状态码
    return new ApiError(status, 'HTTP_ERROR', `请求失败（${status}）`)
  }
  return new ApiError(0, 'NETWORK_ERROR', '网络异常，请稍后重试')
}
