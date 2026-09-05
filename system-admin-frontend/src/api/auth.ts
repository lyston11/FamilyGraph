/**
 * /admin-api/auth/*：登录、refresh 轮换、登出、me、改密、改用户名。
 * 合同：backend/app/api/admin_auth.py + schemas/admin_auth.py。
 */

import { adminRequest } from '@/api/client'
import {
  expectBoolean,
  expectLiteral,
  expectNumber,
  expectObject,
  expectString,
} from '@/api/decode'
import type { AdminSessionOut, AdminTokenPairResponse, LogoutResponse } from '@/types/api'

/**
 * AdminSessionOut 运行时硬校验：登录/refresh 响应缺 admin 字段或字段类型
 * 不符一律抛错，绝不写入会话（state-management spec：登录视图写会话前
 * 必须硬校验主体字段）。
 */
export function decodeAdminSessionOut(raw: unknown): AdminSessionOut {
  const obj = expectObject(raw, 'admin')
  return {
    id: expectNumber(obj['id'], 'admin.id'),
    username: expectString(obj['username'], 'admin.username'),
    password_must_change: expectBoolean(obj['password_must_change'], 'admin.password_must_change'),
    status: expectLiteral(obj['status'], ['managed', 'claimed'] as const, 'admin.status'),
  }
}

function decodeTokenPair(raw: unknown): AdminTokenPairResponse {
  const obj = expectObject(raw, 'token pair')
  return {
    access_token: expectString(obj['access_token'], 'access_token'),
    refresh_token: expectString(obj['refresh_token'], 'refresh_token'),
    token_type: expectString(obj['token_type'], 'token_type'),
    admin: decodeAdminSessionOut(obj['admin']),
  }
}

export interface AdminLoginPayload {
  username: string
  password: string
}

export function apiAdminLogin(payload: AdminLoginPayload): Promise<AdminTokenPairResponse> {
  return adminRequest<unknown>({ method: 'post', url: '/auth/login', data: payload }).then(
    decodeTokenPair,
  )
}

export function apiAdminRefresh(payload: { refresh_token: string }): Promise<AdminTokenPairResponse> {
  return adminRequest<unknown>({ method: 'post', url: '/auth/refresh', data: payload }).then(
    decodeTokenPair,
  )
}

export function apiAdminLogout(payload: { refresh_token: string | null }): Promise<LogoutResponse> {
  return adminRequest<LogoutResponse>({ method: 'post', url: '/auth/logout', data: payload })
}

export function apiAdminMe(): Promise<AdminSessionOut> {
  return adminRequest<unknown>({ method: 'get', url: '/auth/me' }).then(decodeAdminSessionOut)
}

export function apiAdminChangePassword(payload: {
  current_password: string
  new_password: string
}): Promise<AdminSessionOut> {
  return adminRequest<unknown>({ method: 'put', url: '/auth/password', data: payload }).then(
    decodeAdminSessionOut,
  )
}

export function apiAdminChangeUsername(payload: {
  current_password: string
  username: string
}): Promise<AdminSessionOut> {
  return adminRequest<unknown>({ method: 'put', url: '/auth/username', data: payload }).then(
    decodeAdminSessionOut,
  )
}
