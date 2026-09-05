/**
 * 登录视图测试：
 * - 成功登录 → 跳概览（或 redirect 目标）；
 * - password_must_change → 跳强制改密页；
 * - 401 统一文案展示（防枚举），不泄露账号存在性；
 * - 提交中禁止重复提交。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'
import AdminLoginView from '@/views/AdminLoginView.vue'
import { createAdminRouter, setupAdminRouterGuards } from '@/router'
import { useAdminAuthStore } from '@/stores/auth'
import { ADMIN_REFRESH_TOKEN_STORAGE_KEY } from '@/stores/auth'

const mockedAdminRequest = vi.mocked(adminRequest)

/** 懒加载路由组件的解析需要宏任务时间片；轮询等待目标路由。 */
async function waitForRoute(
  router: ReturnType<typeof createAdminRouter>,
  name: string,
  timeoutMs = 500,
): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    await flushPromises()
    if (router.currentRoute.value.name === name) return
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  await flushPromises()
}

async function mountAtLogin() {
  const router = createAdminRouter()
  setupAdminRouterGuards(router)
  await router.push('/login')
  const wrapper = mount(AdminLoginView, {
    global: { plugins: [router] },
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedAdminRequest.mockReset()
})

describe('AdminLoginView', () => {
  it('成功登录跳转概览并写入会话', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
      admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' },
    })
    const { wrapper, router } = await mountAtLogin()

    await wrapper.find('input[data-testid="login-username"]').setValue('admin')
    await wrapper.find('input[data-testid="login-password"]').setValue('Sup3rSecret!pass')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(mockedAdminRequest).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'post',
        url: '/auth/login',
        data: { username: 'admin', password: 'Sup3rSecret!pass' },
      }),
    )
    await waitForRoute(router, 'overview')
    expect(router.currentRoute.value.name).toBe('overview')
    expect(useAdminAuthStore().isLoggedIn).toBe(true)
    expect(localStorage.getItem(ADMIN_REFRESH_TOKEN_STORAGE_KEY)).toBe('refresh-1')
  })

  it('password_must_change 会话登录后进入强制改密页', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      access_token: 'access-1',
      refresh_token: 'refresh-1',
      token_type: 'bearer',
      admin: { id: 1, username: 'admin', password_must_change: true, status: 'managed' },
    })
    const { wrapper, router } = await mountAtLogin()

    await wrapper.find('input[data-testid="login-username"]').setValue('admin')
    await wrapper.find('input[data-testid="login-password"]').setValue('Initial!Password1')
    await wrapper.find('form').trigger('submit')
    await waitForRoute(router, 'force-change-password')
    expect(router.currentRoute.value.name).toBe('force-change-password')
  })

  it('凭据错误显示统一安全文案', async () => {
    mockedAdminRequest.mockRejectedValueOnce(
      new (await import('@/api/client')).AdminApiError(401, 'ADMIN_INVALID_CREDENTIALS', '用户名或密码错误'),
    )
    const { wrapper } = await mountAtLogin()

    await wrapper.find('input[data-testid="login-username"]').setValue('admin')
    await wrapper.find('input[data-testid="login-password"]').setValue('wrong-password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="login-error"]').text()).toBe('用户名或密码错误')
    expect(useAdminAuthStore().isLoggedIn).toBe(false)
  })

  it('提交中禁止重复提交', async () => {
    const deferred: { resolve?: (value: unknown) => void } = {}
    mockedAdminRequest.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          deferred.resolve = () =>
            resolve({
              access_token: 'a',
              refresh_token: 'r',
              token_type: 'bearer',
              admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' },
            })
        }),
    )
    const { wrapper } = await mountAtLogin()
    await wrapper.find('input[data-testid="login-username"]').setValue('admin')
    await wrapper.find('input[data-testid="login-password"]').setValue('Sup3rSecret!pass')
    await wrapper.find('form').trigger('submit')
    expect((wrapper.find('[data-testid="login-submit"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
    deferred.resolve?.(undefined)
    await flushPromises()
  })
})
