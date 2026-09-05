/**
 * admin router guard 测试（design §2 / PRD FE-F2）：
 * - 未登录访问业务页 → /login（带 redirect query）；
 * - 未登录访问登录页 → 放行；
 * - 已登录 + password_must_change → 强制改密页之外一律拦截；
 * - 已登录访问登录页/强制改密页 → 回概览；
 * - session expired（清会话）后回 /login，绝不出现家庭路径；
 * - redirect 只接受内部路径（resolveSafeRedirect 白名单）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'
import { createAdminRouter, setupAdminRouterGuards } from '@/router'
import { resolveSafeRedirect } from '@/router/safeRedirect'
import { ADMIN_REFRESH_TOKEN_STORAGE_KEY, useAdminAuthStore } from '@/stores/auth'

const mockedAdminRequest = vi.mocked(adminRequest)

const PAIR = {
  access_token: 'access-1',
  refresh_token: 'refresh-1',
  token_type: 'bearer',
  admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' as const },
}

async function loginAs(passwordMustChange: boolean): Promise<void> {
  mockedAdminRequest.mockResolvedValueOnce({
    ...PAIR,
    admin: { ...PAIR.admin, password_must_change: passwordMustChange },
  })
  const auth = useAdminAuthStore()
  await auth.login('admin', 'Sup3rSecret!pass')
}

function freshRouter() {
  const router = createAdminRouter()
  setupAdminRouterGuards(router)
  return router
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedAdminRequest.mockReset()
})

describe('admin router guard', () => {
  it('未登录访问业务页 → 登录页并携带内部 redirect', async () => {
    const router = freshRouter()
    await router.push('/spaces/3')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query['redirect']).toBe('/spaces/3')
  })

  it('未登录访问登录页 → 放行', async () => {
    const router = freshRouter()
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('已登录普通会话访问业务页 → 放行', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('password_must_change 会话访问业务页 → 强制改密页', async () => {
    await loginAs(true)
    const router = freshRouter()
    await router.push('/operations')
    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('password_must_change 会话访问强制改密页 → 放行', async () => {
    await loginAs(true)
    const router = freshRouter()
    await router.push('/force-change-password')
    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('已改密会话访问强制改密页 → 回概览', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/force-change-password')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('已登录访问登录页 → 回概览', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/login')
    expect(router.currentRoute.value.name).toBe('overview')
  })

  it('session expired（clearSession）后访问业务页 → 登录页，绝无家庭路径', async () => {
    await loginAs(false)
    const auth = useAdminAuthStore()
    const router = freshRouter()
    await router.push('/')

    auth.clearSession()
    localStorage.removeItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)
    await router.push('/agent')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('未知路径（已登录）→ NotFound 安全空态', async () => {
    await loginAs(false)
    const router = freshRouter()
    await router.push('/totally-unknown/path')
    expect(router.currentRoute.value.name).toBe('not-found')
  })

  it('redirect 白名单：拒绝外部/协议相对/家庭路径', () => {
    expect(resolveSafeRedirect('/spaces/1')).toBe('/spaces/1')
    expect(resolveSafeRedirect('https://evil.example.com/x')).toBeNull()
    expect(resolveSafeRedirect('//evil.example.com')).toBeNull()
    expect(resolveSafeRedirect('http://localhost:5174/login')).toBeNull()
    expect(resolveSafeRedirect('/login')).toBeNull()
    expect(resolveSafeRedirect('spaces/1')).toBeNull()
    expect(resolveSafeRedirect(undefined)).toBeNull()
  })
})
