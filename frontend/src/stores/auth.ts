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
  /**
   * 主体判定唯一来源（Phase 6 审计结论固定）：只看服务端签名会话返回的
   * `principal_type === 'system_admin'`（/auth/login、/auth/login/select、/auth/refresh
   * 的 TokenPairResponse 与 GET /me；系统主体按设计不调 /me，refresh 响应即权威投影）。
   * `is_admin` 是 v2 兼容显示字段，platform_role 是家庭端平台运营标记——两者都不参与
   * 主体互斥判定（architecture.md §0.8：JWT 必须携带 principal_type）。
   */
  const isSystemAdmin = computed(() => user.value?.principal_type === 'system_admin')
  const isPlatformOperator = computed(() => isSystemAdmin.value)

  /**
   * 家庭侧敏感缓存清理（不含凭据与 user 投影）：
   * - 登出 / 401 / token 失效：clearSession 全量清空；
   * - 登录换入 system_admin 主体（见下方 login/selectCandidate）：同样全量清空，
   *   保证系统管理员会话与家庭会话互斥切换时家庭 stores 不残留任何 PII。
   */
  function clearFamilyCaches(): void {
    // 敏感缓存清理红线（state-management.md）：同步清空业务 store 的 PII
    useMembersStore().clear()
    // v2 治理缓存（确档/数据权利/争议）随会话清空，避免身份切换后残留
    void import('@/stores/governance').then((m) => m.useGovernanceStore().clear())
    // Agent 会话/SSE/草稿（V2.2）：关流、清分区、删 sessionStorage Run 游标
    void import('@/stores/agent').then((m) => m.useAgentStore().clear())
    // 记忆候选、已确认 Memory 与 RAG 引用（V2.5）随会话清空
    void import('@/stores/memory').then((m) => m.useMemoryStore().clear())
    // 称谓缓存与解析态（V2.3）随会话清空
    void import('@/stores/kinship').then((m) => m.useKinshipStore().clear())
    // 管家建议卡片（V2.4）随会话清空
    void import('@/stores/actionCards').then((m) => m.useActionCardsStore().clear())
    // 亲属推荐按空间缓存的只读候选随会话清空
    void import('@/stores/familyRecommendations')
      .then((m) => m.useFamilyRecommendationsStore().clear())
    // PersonalFamilyView 按空间缓存的授权投影随会话清空
    void import('@/stores/personalFamilyView').then((m) => m.usePersonalFamilyViewStore().clear())
    // HouseholdCard / 通知 / 空间统计的空间键控缓存随会话清空（09-01 Phase 1 合同层）
    void import('@/stores/household').then((m) => m.useHouseholdCardStore().clear())
    void import('@/stores/notifications').then((m) => m.useNotificationsStore().clear())
    void import('@/stores/spaceStats').then((m) => m.useSpaceStatsStore().clear())
    // 延迟导入避免循环依赖：graph/spaces 依赖 auth 时经由函数内解析
    void import('@/stores/graph').then((m) => m.useGraphStore().clear())
    void import('@/stores/spaces').then((m) => m.useSpacesStore().clear())
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

  /**
   * 主体互斥的登录处理（architecture.md §0.8 / PRD §2.7）：唯一登录端点同时服务
   * 家庭用户与系统管理员。若登录响应是 system_admin 主体，先清空全部家庭敏感
   * store 再落 token——系统管理员进入后台壳时，家庭 stores 必须为空。
   */
  function ensureNoFamilyCachesForPrincipal(pair: TokenPairResponse): void {
    if (pair.user.principal_type === 'system_admin') {
      clearFamilyCaches()
    }
  }

  /**
   * 系统管理员登录会话落位（SAR-F1，09-01-system-admin-governance-routes）：
   * 仅接受 `principal_type === 'system_admin'` 的登录响应。家庭主体 / 未知主体
   * 不得建立系统管理员会话——丢弃凭据并清理任何临时 auth 状态（含残留
   * refresh token），调用方（登录页）负责展示统一拒绝文案。
   */
  function applySystemAdminSession(pair: TokenPairResponse): void {
    if (pair.user?.principal_type !== 'system_admin') {
      clearSession()
      throw new Error('登录响应主体不是 system_admin，已拒绝建立系统管理员会话')
    }
    clearFamilyCaches()
    applyTokenPair(pair)
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
    ensureNoFamilyCachesForPrincipal(pair)
    applyTokenPair(pair)
    return pair
  }

  async function selectCandidate(
    challengeId: string,
    userId: number,
  ): Promise<TokenPairResponse> {
    const pair = await authApi.selectCandidate(challengeId, userId)
    ensureNoFamilyCachesForPrincipal(pair)
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
      // GET /me 是家庭端点，系统管理员访问按设计被拒（401）。系统主体的身份
      // 投影直接采用 refresh 响应：该响应由服务端按签名 token 查表签发，
      // 是权威来源，不是客户端自带字段。
      // pin_must_change=true 时 /me 也被门禁拦截（白名单外 403），同样直接采用。
      const skipFetchMe =
        pair.user.principal_type === 'system_admin' || pair.user.pin_must_change
      if (!skipFetchMe) {
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
    isSystemAdmin,
    isPlatformOperator,
    checkBootstrap,
    login,
    selectCandidate,
    applySystemAdminSession,
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

/**
 * 会话过期整页跳转（主体感知，SAR-F2）：system_admin 会话失效回
 * `/system-admin/login`，family_user 回 `/login`——系统管理员 token 过期
 * 绝不降级为家庭登录入口，反之亦然。principal 由调用方在 clearSession
 * 之前从 auth store 快照（clearSession 后 user 已为 null，无法再读）。
 *
 * 已在目标登录页时不再 assign：初始导航未解析窗口内 AppShell 可能发起
 * 未认证请求（401），重复 assign 会造成登录页无限整页重载循环
 * （09-01 走查实测：2320 次循环请求）。
 */
export const sessionExpiredNavigator = { assign: (url: string) => window.location.assign(url) }
export function sessionExpiredRedirect(wasSystemAdmin = false): void {
  const target = wasSystemAdmin ? '/system-admin/login' : '/login'
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
    // 主体快照必须先于 clearSession 读取：清空后 principal_type 不可再判定
    const wasSystemAdmin = store.isSystemAdmin
    store.clearSession()
    sessionExpiredRedirect(wasSystemAdmin)
  })
}
