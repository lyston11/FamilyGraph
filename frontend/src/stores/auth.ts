import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import { useActionCardsStore } from '@/stores/actionCards'
import { useAgentStore } from '@/stores/agent'
import { useFamilyRecommendationsStore } from '@/stores/familyRecommendations'
import { useGovernanceStore } from '@/stores/governance'
import { useGraphStore } from '@/stores/graph'
import { useHouseholdCardStore } from '@/stores/household'
import { useKinshipStore } from '@/stores/kinship'
import { useMembersStore } from '@/stores/members'
import { useMemoryStore } from '@/stores/memory'
import { useNotificationsStore } from '@/stores/notifications'
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpaceStatsStore } from '@/stores/spaceStats'
import { useSpacesStore } from '@/stores/spaces'
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
 * - 09-04：家庭端只有 family_user 主体；系统管理员在独立前端应用与会话域，
 *   本 store 不包含任何后台主体分支。
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
  /**
   * 注册开关（09-05 决策 2）：REGISTRATION_ENABLED 的运行时投影，随
   * /bootstrap/status 一次性取得。默认 true：开关是部署级常量，会话内不反转；
   * 投影缺失（部署偏斜/旧 fixture）按可用处理，关闭由后端端点 404 兜底。
   */
  const registrationEnabled = ref(true)

  // ---- 派生 ----
  const isLoggedIn = computed(() => accessToken.value !== null && user.value !== null)
  const mustChangePin = computed(() => user.value?.pin_must_change === true)

  /**
   * 家庭侧敏感缓存清理（不含凭据与 user 投影）：
   * 登出 / 401 / token 失效时 clearSession 全量清空，业务 store 不残留任何 PII。
   *
   * 全部静态导入（09-11 R6）：这些 store 均已被路由/视图/组合式函数静态依赖，
   * 动态 import 不再产生任何拆包收益，只会制造 rollup 动态/静态冲突警告。
   * agent/spaces 虽静态导入 auth，但其 useAuthStore 调用均为函数级（运行时
   * 解析），静态循环无初始化顺序问题。
   */
  function clearFamilyCaches(): void {
    useMembersStore().clear()
    useGovernanceStore().clear()
    useAgentStore().clear()
    useMemoryStore().clear()
    useKinshipStore().clear()
    useActionCardsStore().clear()
    useFamilyRecommendationsStore().clear()
    usePersonalFamilyViewStore().clear()
    useHouseholdCardStore().clear()
    useNotificationsStore().clear()
    useStewardSuggestionsStore().clear()
    useSpaceStatsStore().clear()
    useGraphStore().clear()
    useSpacesStore().clear()
  }

  function clearSession(): void {
    accessToken.value = null
    refreshToken.value = null
    user.value = null
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    clearFamilyCaches()
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
      registrationEnabled.value = status.registration_enabled ?? true
      bootstrapChecked.value = true
    }
    return systemInitialized.value
  }

  async function login(name: string, pin: string): Promise<TokenPairResponse> {
    const pair = await authApi.login(name, pin)
    applyTokenPair(pair)
    return pair
  }

  /**
   * 自助注册（09-05）：token 落地与 login 走同一条 applyTokenPair 路径——
   * 响应即登录态；新用户 profile_status=provisional 由路由守卫引导确档。
   */
  async function register(name: string, pin: string, code?: string): Promise<TokenPairResponse> {
    const pair = await authApi.register({ name, pin, code })
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
      // pin_must_change=true 时 /me 被门禁拦截（白名单外 403），直接采用
      // refresh 响应自带的家庭身份投影，不额外请求。
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

  return {
    accessToken,
    refreshToken,
    user,
    bootstrapChecked,
    systemInitialized,
    registrationEnabled,
    isLoggedIn,
    mustChangePin,
    checkBootstrap,
    login,
    register,
    selectCandidate,
    refreshSession,
    resume,
    logout,
    changePin,
    updateName,
    markInitialized(): void {
      systemInitialized.value = true
      bootstrapChecked.value = true
    },
    clearSession,
  }
})

/**
 * 会话过期整页跳转：家庭会话失效一律回 `/login`。系统管理员会话在
 * 独立前端应用内自行处理，与家庭跳转路径互不相干（09-04 隔离合同）。
 *
 * 已在目标登录页时不再 assign：初始导航未解析窗口内 AppShell 可能发起
 * 未认证请求（401），重复 assign 会造成登录页无限整页重载循环
 * （09-01 走查实测：2320 次循环请求）。
 */
export const sessionExpiredNavigator = { assign: (url: string) => window.location.assign(url) }
export function sessionExpiredRedirect(): void {
  const target = '/login'
  if (window.location.pathname !== target) {
    sessionExpiredNavigator.assign(target)
  }
}

/** 应用启动接线：把 store 能力注册给 api/client（避免模块循环导入） */
export function wireAuthInterceptors(): void {
  const store = useAuthStore()
  registerTokenReader(() => store.accessToken)
  registerRefreshExecutor(() => store.refreshSession())
  registerSessionExpiredHandler(() => {
    store.clearSession()
    sessionExpiredRedirect()
  })
}
