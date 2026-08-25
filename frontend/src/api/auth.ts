import type {
  BootstrapStatusResponse,
  InitializeResponse,
  TokenPairResponse,
  UserOut,
} from '@/types/api'

import { apiClient, rawClient } from './client'

export async function login(name: string, pin: string): Promise<TokenPairResponse> {
  const { data } = await apiClient.post<TokenPairResponse>('/auth/login', { name, pin })
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

export async function initializeAdmin(name: string): Promise<InitializeResponse> {
  const { data } = await apiClient.post<InitializeResponse>('/bootstrap/initialize', { name })
  return data
}
