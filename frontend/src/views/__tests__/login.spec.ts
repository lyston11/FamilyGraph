import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import { ApiError } from '@/api/errors'
import * as authApi from '@/api/auth'
import LoginView from '@/views/LoginView.vue'
import type { TokenPairResponse } from '@/types/api'

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  selectCandidate: vi.fn(),
  refreshTokens: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePin: vi.fn(),
  changeName: vi.fn(),
  fetchBootstrapStatus: vi.fn().mockResolvedValue({ initialized: true }),
  initializeAdmin: vi.fn(),
}))

const mockedLogin = vi.mocked(authApi.login)
const mockedSelect = vi.mocked(authApi.selectCandidate)

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user: { id: 1, name: '张三', is_admin: false, pin_must_change: false, ...overrides },
  }
}

// el-dialog 默认 teleport 到 body，弹窗断言走 document 查询
const dialogInBody = (): Element | null =>
  document.querySelector('[data-test="challenge-dialog"]')

async function mountView() {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      {
        path: '/force-change-pin',
        name: 'force-change-pin',
        component: { template: '<div />' },
      },
    ],
  })
  const wrapper = mount(LoginView, {
    global: { plugins: [pinia, router, ElementPlus] },
    attachTo: document.body,
  })
  await router.isReady()
  return { wrapper, router }
}

describe('LoginView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('渲染登录表单', async () => {
    const { wrapper } = await mountView()

    expect(wrapper.find('[data-test="login-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="login-pin"]').exists()).toBe(true)
    expect(wrapper.find('h1').text()).toBe('FamilyGraph')
    wrapper.unmount()
  })

  it('登录成功：调用 API 并持久化 refresh token', async () => {
    mockedLogin.mockResolvedValue(makePair())
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="login-name"]').setValue('张三')
    await wrapper.find('[data-test="login-pin"]').setValue('123456')
    await wrapper.find('[data-test="login-submit"]').trigger('click')

    await vi.waitFor(() => expect(mockedLogin).toHaveBeenCalledWith('张三', '123456'))
    expect(localStorage.getItem('fg.refresh_token')).toBe('refresh-xyz')
    wrapper.unmount()
  })

  it('凭据错误：展示后端统一文案（防枚举），不调用消歧', async () => {
    mockedLogin.mockRejectedValue(
      new ApiError(401, 'AUTH_INVALID_CREDENTIALS', '名字或 PIN 码错误'),
    )
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="login-name"]').setValue('张三')
    await wrapper.find('[data-test="login-pin"]').setValue('000000')
    await wrapper.find('[data-test="login-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="login-error"]').text()).toBe('名字或 PIN 码错误'),
    )
    expect(mockedSelect).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('本地校验：PIN 非 6 位数字时直接提示', async () => {
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="login-name"]').setValue('张三')
    await wrapper.find('[data-test="login-pin"]').setValue('123')
    await wrapper.find('[data-test="login-submit"]').trigger('click')

    expect(wrapper.find('[data-test="login-error"]').text()).toContain('6 位数字')
    expect(mockedLogin).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('同名同 PIN 消歧：409 弹出候选列表 → 选择后签发会话', async () => {
    mockedLogin.mockRejectedValue(
      new ApiError(409, 'CHALLENGE_REQUIRED', '存在同名账号', {
        challenge_id: 'chal-1',
        candidates: [
          { id: 1, name: '大壮' },
          { id: 2, name: '大壮' },
        ],
      }),
    )
    mockedSelect.mockResolvedValue(makePair({ id: 2, name: '大壮' }))
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="login-name"]').setValue('大壮')
    await wrapper.find('[data-test="login-pin"]').setValue('123456')
    await wrapper.find('[data-test="login-submit"]').trigger('click')

    // 弹窗出现且列出两个候选
    await vi.waitFor(() => expect(dialogInBody()).not.toBeNull())
    const radios = dialogInBody()!.querySelectorAll('.candidate-list .el-radio')
    expect(radios.length).toBe(2)

    radios[1].dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))

    const confirm = document.querySelector<HTMLButtonElement>('[data-test="challenge-confirm"]')!
    expect(confirm.disabled).toBe(false)
    confirm.click()

    await vi.waitFor(() => expect(mockedSelect).toHaveBeenCalledWith('chal-1', 2))
    expect(localStorage.getItem('fg.refresh_token')).toBe('refresh-xyz')
    wrapper.unmount()
  })

  it('challenge 重放/过期被拒：关闭弹窗并提示重新登录', async () => {
    mockedLogin.mockRejectedValue(
      new ApiError(409, 'CHALLENGE_REQUIRED', '存在同名账号', {
        challenge_id: 'chal-1',
        candidates: [{ id: 1, name: '大壮' }],
      }),
    )
    mockedSelect.mockRejectedValue(
      new ApiError(401, 'CHALLENGE_INVALID', '登录校验已失效，请重新登录'),
    )
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="login-name"]').setValue('大壮')
    await wrapper.find('[data-test="login-pin"]').setValue('123456')
    await wrapper.find('[data-test="login-submit"]').trigger('click')
    await vi.waitFor(() => expect(dialogInBody()).not.toBeNull())

    const radio = dialogInBody()!.querySelector('.candidate-list .el-radio')!
    radio.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))
    ;(document.querySelector('[data-test="challenge-confirm"]') as HTMLButtonElement).click()

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="login-error"]').text()).toContain('重新登录'),
    )
    expect(localStorage.getItem('fg.refresh_token')).toBeNull()
    wrapper.unmount()
  })
})
