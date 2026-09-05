/**
 * 管理员认证 store（Pinia setup 风格）。
 *
 * 存储红线：
 * - access token 只存内存（页面刷新即失效，靠 refresh 恢复）；
 * - refresh token 使用独立 key `fg.admin.refresh_token`，绝不与家庭
 *   `fg.refresh_token` 共用；
 * - logout/401/refresh 失败时清空全部内存态 + 独立 refresh key + 敏感票据，
 *   会话过期只回后台登录页（绝不降级到家庭登录）。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  apiAdminChangePassword,
  apiAdminChangeUsername,
  apiAdminLogin,
  apiAdminLogout,
  apiAdminRefresh,
} from '@/api/auth'
import type { AdminSessionOut } from '@/types/api'
import { useAdminAccessSessionStore } from '@/stores/accessSession'

/** 独立命名空间 key（家庭端使用 fg.refresh_token，绝不可混用）。 */
export const ADMIN_REFRESH_TOKEN_STORAGE_KEY = 'fg.admin.refresh_token'

export const useAdminAuthStore = defineStore('adminAuth', () => {
  // ---- 内存态（绝不持久化）----
  const accessToken = ref<string | null>(null)
  const admin = ref<AdminSessionOut | null>(null)
  const restoring = ref(false)

  const isLoggedIn = computed(() => accessToken.value !== null && admin.value !== null)
  const mustChangePassword = computed(() => admin.value?.password_must_change === true)

  function readStoredRefreshToken(): string | null {
    return localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)
  }

  function applySession(pair: {
    access_token: string
    refresh_token: string
    admin: AdminSessionOut
  }): void {
    accessToken.value = pair.access_token
    admin.value = pair.admin
    localStorage.setItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY, pair.refresh_token)
  }

  function clearMemory(): void {
    accessToken.value = null
    admin.value = null
  }

  /** 清空全部会话态（内存 + 独立 refresh key + 敏感票据）。 */
  function clearSession(): void {
    clearMemory()
    localStorage.removeItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)
    // 敏感票据只存内存：清会话时同步失效
    useAdminAccessSessionStore().clearAll()
  }

  /** 登录：api 层已硬校验 admin 主体字段；返回是否需要强制改密。 */
  async function login(username: string, password: string): Promise<boolean> {
    const pair = await apiAdminLogin({ username, password })
    applySession(pair)
    return pair.admin.password_must_change
  }

  /** 应用启动时恢复会话：用独立 key 中的 refresh token 轮换。 */
  async function restoreSession(): Promise<boolean> {
    const stored = readStoredRefreshToken()
    if (!stored) return false
    restoring.value = true
    try {
      return await requestRefresh()
    } finally {
      restoring.value = false
    }
  }

  /** 实际轮换请求（成功写新会话，失败清会话）。 */
  async function requestRefresh(): Promise<boolean> {
    const stored = readStoredRefreshToken()
    if (!stored) return false
    try {
      const pair = await apiAdminRefresh({ refresh_token: stored })
      applySession(pair)
      return true
    } catch {
      clearSession()
      return false
    }
  }

  // 单飞在途轮换：并发 401 复用同一次请求。否则两个并发请求各带同一份
  // 旧 refresh token 轮换，第二笔会被后端判为重放（重用）→ 撤销全部会话。
  let refreshInFlight: Promise<boolean> | null = null

  /** 401 时由 api client 调用：成功轮换返回 true（请求会重试一次）。 */
  async function tryRefresh(): Promise<boolean> {
    if (refreshInFlight === null) {
      refreshInFlight = requestRefresh().finally(() => {
        refreshInFlight = null
      })
    }
    return refreshInFlight
  }

  /** 登出：撤销远端 refresh 会话后清空本地全部状态。 */
  async function logout(): Promise<void> {
    const refresh = readStoredRefreshToken()
    try {
      if (accessToken.value !== null) {
        await apiAdminLogout({ refresh_token: refresh })
      }
    } finally {
      clearSession()
    }
  }

  /**
   * 修改密码（需当前密码）：后端成功后撤销全部会话（password_version+1），
   * 因此无论首登强制改密还是账号设置，成功后本地会话一律失效。
   */
  async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
    try {
      await apiAdminChangePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
    } finally {
      // 远端已撤销全部会话；本地必须同步清理（含 refresh key 与票据）
      clearSession()
    }
  }

  /** 修改用户名（需当前密码）：后端撤销全部会话，成功后同样回登录页。 */
  async function changeUsername(currentPassword: string, newUsername: string): Promise<void> {
    try {
      await apiAdminChangeUsername({
        current_password: currentPassword,
        username: newUsername,
      })
    } finally {
      clearSession()
    }
  }

  return {
    accessToken,
    admin,
    restoring,
    isLoggedIn,
    mustChangePassword,
    login,
    restoreSession,
    tryRefresh,
    logout,
    clearSession,
    changePassword,
    changeUsername,
  }
})
