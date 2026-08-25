/**
 * 后端 API 类型定义：与 backend/app/schemas/auth.py 字段一一对应（人工同步）。
 * 错误结构见 spec/backend/error-handling.md。
 */

export interface UserOut {
  id: number
  name: string
  is_admin: boolean
  pin_must_change: boolean
}

export interface TokenPairResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: UserOut
}

/** 同名同 PIN 消歧 409 响应体（architecture.md §2 AD-2） */
export interface ChallengeResponse {
  challenge_id: string
  candidates: ChallengeCandidate[]
}

export interface ChallengeCandidate {
  id: number
  name: string
}

export interface BootstrapStatusResponse {
  initialized: boolean
}

export interface InitializeResponse {
  user: UserOut
  one_time_pin: string
}

/** 统一错误外壳 */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    detail?: unknown
  }
}

export const ERROR_CODES = {
  AUTH_UNAUTHORIZED: 'AUTH_UNAUTHORIZED',
  AUTH_INVALID_CREDENTIALS: 'AUTH_INVALID_CREDENTIALS',
  ACCOUNT_LOCKED: 'ACCOUNT_LOCKED',
  CHALLENGE_INVALID: 'CHALLENGE_INVALID',
  INVALID_REFRESH_TOKEN: 'INVALID_REFRESH_TOKEN',
  PIN_CHANGE_REQUIRED: 'PIN_CHANGE_REQUIRED',
  BOOTSTRAP_ALREADY_INITIALIZED: 'BOOTSTRAP_ALREADY_INITIALIZED',
} as const

export type ErrorCode = (typeof ERROR_CODES)[keyof typeof ERROR_CODES]
