import type { BootstrapStatusResponse, TokenPairResponse, UserOut } from '@/types/api'

import { apiClient, rawClient } from './client'

export async function login(name: string, pin: string): Promise<TokenPairResponse> {
  const { data } = await apiClient.post<TokenPairResponse>('/auth/login', { name, pin })
  return data
}

/**
 * 自助注册（09-05 决策 3）：用户名（即登录名与档案名，users.name 单字段）+
 * 自选 PIN + 可选邀请码。响应即登录态（TokenPairResponse，与 login 同形）。
 *
 * 不发送 display_name：后端已将显示名并入用户名（RegisterRequest.display_name
 * 仅为兼容入参，单名字数据模型下不单独持久化），UI 只收用户名。
 * 成功路径与防枚举/字段级错误文案一律原样呈现后端 message（error-handling.md）。
 */
export async function register(payload: {
  name: string
  pin: string
  code?: string
}): Promise<TokenPairResponse> {
  const body: Record<string, string> = { name: payload.name, pin: payload.pin }
  if (payload.code) {
    body.code = payload.code
  }
  const { data } = await apiClient.post<TokenPairResponse>('/auth/register', body)
  return data
}

/** 同名同 PIN 消歧第二步 */
export async function selectCandidate(
  challengeId: string,
  userId: number,
): Promise<TokenPairResponse> {
  const { data } = await apiClient.post<TokenPairResponse>('/auth/login/select', {
    challenge_id: challengeId,
    user_id: userId,
  })
  return data
}

/** 刷新走裸实例：不经过认证拦截器，避免循环刷新（client.ts） */
export async function refreshTokens(refreshToken: string): Promise<TokenPairResponse> {
  const { data } = await rawClient.post<TokenPairResponse>('/auth/refresh', {
    refresh_token: refreshToken,
  })
  return data
}

export async function logout(refreshToken: string | null): Promise<void> {
  await apiClient.post('/auth/logout', { refresh_token: refreshToken })
}

export async function fetchMe(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/me')
  return data
}

export async function changePin(oldPin: string, newPin: string): Promise<UserOut> {
  const { data } = await apiClient.put<UserOut>('/me/pin', {
    old_pin: oldPin,
    new_pin: newPin,
  })
  return data
}

export async function changeName(name: string): Promise<UserOut> {
  const { data } = await apiClient.put<UserOut>('/me/name', { name })
  return data
}

export async function fetchBootstrapStatus(): Promise<BootstrapStatusResponse> {
  const { data } = await apiClient.get<BootstrapStatusResponse>('/bootstrap/status')
  return data
}
