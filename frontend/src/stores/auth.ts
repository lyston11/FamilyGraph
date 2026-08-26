import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import { useMembersStore } from '@/stores/members'
import {
  registerRefreshExecutor,
  registerSessionExpiredHandler,
  registerTokenReader,
} from '@/api/client'
import type { TokenPairResponse, UserOut } from '@/types/api'

/**
 * 认证状态（spec/frontend/state-management.md 红线）：
 * - access token 只存内存；localStorage 仅允许 refresh token
 * - 登出 / 401 / token 失效时清空全部状态与 localStorage，路由守卫兜底跳登录页
 */
const REFRESH_TOKEN_KEY = 'fg.refresh_token'

export const useAuthStore = defineStore('auth', () => {
  // ---- 状态 ----
  const accessToken = ref<string | null>(null)
  const refreshToken = ref<string | null>(localStorage.getItem(REFRESH_TOKEN_KEY))
  const user = ref<UserOut | null>(null)
  /** 引导状态是否已确认（避免每次路由跳转都请求 /bootstrap/status） */
  const bootstrapChecked = ref(false)
  const systemInitialized = ref(false)

  // ---- 派生 ----
  const isLoggedIn = computed(() => accessToken.value !== null && user.value !== null)
  const mustChangePin = computed(() => user.value?.pin_must_change === true)

  function clearSession(): void {
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    // 敏感缓存清理红线（state-management.md）：同步清空业务 store 的 PII
    useMembersStore().clear()
    // v2 治理缓存（确档/数据权利/争议）随会话清空，避免身份切换后残留
    void import('@/stores/governance').then((m) => m.useGovernanceStore().clear())
    // Agent 会话/SSE/草稿（V2.2）：关流、清分区、删 sessionStorage Run 游标
    void import('@/stores/agent').then((m) => m.useAgentStore().clear())
    // 称谓缓存与解析态（V2.3）随会话清空
    void import('@/stores/kinship').then((m) => m.useKinshipStore().clear())
    // 管家建议卡片（V2.4）随会话清空
    void import('@/stores/actionCards').then((m) => m.useActionCardsStore().clear())
    // 延迟导入避免循环依赖：graph/spaces 依赖 auth 时经由函数内解析
    void import('@/stores/graph').then((m) => m.useGraphStore().clear())
    void import('@/stores/spaces').then((m) => m.useSpacesStore().clear())
  }

  function applyTokenPair(pair: TokenPairResponse): void {
    accessToken.value = pair.access_token
    refreshToken.value = pair.refresh_token
    user.value = pair.user
    localStorage.setItem(REFRESH_TOKEN_KEY, pair.refresh_token)
  }

  // ---- 动作 ----
  async function checkBootstrap(): Promise<boolean> {
    if (!bootstrapChecked.value) {
      const status = await authApi.fetchBootstrapStatus()
      systemInitialized.value = status.initialized
      bootstrapChecked.value = true
    }
    return systemInitialized.value
  }

  async function login(name: string, pin: string): Promise<TokenPairResponse> {
    const pair = await authApi.login(name, pin)
    applyTokenPair(pair)
    return pair
  }

  async function selectCandidate(
    challengeId: string,
    userId: number,
  ): Promise<TokenPairResponse> {
    const pair = await authApi.selectCandidate(challengeId, userId)
    applyTokenPair(pair)
    return pair
  }

  /** 刷新会话；由 api/client 拦截器经注册回调调用（单飞行在拦截器层控制） */
  async function refreshSession(): Promise<TokenPairResponse> {
    if (!refreshToken.value) throw new Error('no refresh token')
    const pair = await authApi.refreshTokens(refreshToken.value)
    applyTokenPair(pair)
    return pair
  }

  /** 硬刷新页面后恢复会话 */
  async function resume(): Promise<UserOut | null> {
    if (!refreshToken.value) return null
    try {
      const pair = await refreshSession()
      // pin_must_change=true 时 GET /me 被服务端门禁拦截（白名单外 403），
      // 直接采用 refresh 响应自带的 user，让强制改 PIN 态在硬刷新后保持
      if (!pair.user.pin_must_change) {
        user.value = await authApi.fetchMe()
      }
      return user.value
    } catch {
      clearSession()
      return null
    }
  }

  async function logout(): Promise<void> {
    try {
      if (accessToken.value) {
        await authApi.logout(refreshToken.value)
      }
    } finally {
      clearSession()
    }
  }

  /** 改 PIN：服务端使全部旧会话即刻失效，本地同步清理并回登录页 */
  async function changePin(oldPin: string, newPin: string): Promise<UserOut> {
    const updated = await authApi.changePin(oldPin, newPin)
    clearSession()
    return updated
  }

  async function updateName(name: string): Promise<UserOut> {
    const updated = await authApi.changeName(name)
    user.value = updated
    return updated
  }

  async function initializeAdmin(name: string): Promise<string> {
    const result = await authApi.initializeAdmin(name)
    systemInitialized.value = true
    bootstrapChecked.value = true
    return result.one_time_pin
  }

  return {
    accessToken,
    refreshToken,
    user,
    bootstrapChecked,
    systemInitialized,
    isLoggedIn,
    mustChangePin,
    checkBootstrap,
    login,
    selectCandidate,
    refreshSession,
    resume,
    logout,
    changePin,
    updateName,
    initializeAdmin,
    markInitialized(): void {
      systemInitialized.value = true
      bootstrapChecked.value = true
    },
    clearSession,
  }
})

/** 应用启动接线：把 store 能力注册给 api/client（避免模块循环导入） */
export function wireAuthInterceptors(): void {
  const store = useAuthStore()
  registerTokenReader(() => store.accessToken)
  registerRefreshExecutor(() => store.refreshSession())
  registerSessionExpiredHandler(() => {
    store.clearSession()
    window.location.assign('/login')
  })
}
