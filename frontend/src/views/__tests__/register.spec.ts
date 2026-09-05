import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import { ApiError } from '@/api/errors'
import * as authApi from '@/api/auth'
import OnboardingView from '@/views/OnboardingView.vue'
import RegisterView from '@/views/RegisterView.vue'
import { useAuthStore } from '@/stores/auth'
import type { TokenPairResponse } from '@/types/api'

/**
 * 09-05 Chunk E 前端测试：注册表单校验 / ?code= 回填 / 成功跳转 /
 * 防枚举与码字段级文案原样呈现；OnboardingView 冷启动注册引导与开关隐藏。
 */

vi.mock('@/api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  selectCandidate: vi.fn(),
  refreshTokens: vi.fn(),
  logout: vi.fn(),
  fetchMe: vi.fn(),
  changePin: vi.fn(),
  changeName: vi.fn(),
  fetchBootstrapStatus: vi.fn().mockResolvedValue({ initialized: true, registration_enabled: true }),
}))

const mockedRegister = vi.mocked(authApi.register)

function makePair(overrides: Partial<TokenPairResponse['user']> = {}): TokenPairResponse {
  return {
    access_token: 'access-abc',
    refresh_token: 'refresh-xyz',
    token_type: 'bearer',
    user: {
      id: 1,
      name: '张三',
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'provisional',
      ...overrides,
    },
  }
}

const MessageProvided = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(RegisterView))])
  },
})

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
      { path: '/register', name: 'register', component: RegisterView },
      { path: '/force-change-pin', name: 'force-change-pin', component: { template: '<div />' } },
    ],
  })
}

async function mountRegister(router: Router, pinia: Pinia): Promise<ReturnType<typeof mount>> {
  const wrapper = mount(MessageProvided, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await router.isReady()
  await flushPromises()
  return wrapper
}

describe('RegisterView', () => {
  let pinia: Pinia
  let router: Router

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    document.body.innerHTML = ''
    pinia = createPinia()
    router = makeRouter()
  })

  it('渲染注册表单：用户名 / PIN / 确认 PIN / 可选邀请码', async () => {
    await router.push({ name: 'register' })
    const wrapper = await mountRegister(router, pinia)

    expect(wrapper.find('[data-test="register-name"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="register-pin"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="register-pin-confirm"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="register-code"]').exists()).toBe(true)
    expect(wrapper.find('h1').text()).toBe('注册新账号')
    wrapper.unmount()
  })

  it('?code= 自动回填并归一大写（分享链接落地端，决策 12）', async () => {
    await router.push({ name: 'register', query: { code: 'ab2d3f5h' } })
    const wrapper = await mountRegister(router, pinia)

    const codeInput = wrapper.find('[data-test="register-code"] input').element as HTMLInputElement
    expect(codeInput.value).toBe('AB2D3F5H')
    wrapper.unmount()
  })

  it('注册成功：调用 API（无码不带 code 字段语义）、token 落地并跳转 home', async () => {
    mockedRegister.mockResolvedValue(makePair())
    await router.push({ name: 'register' })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123456')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedRegister).toHaveBeenCalledWith({ name: '张三', pin: '123456', code: undefined }),
    )
    expect(localStorage.getItem('fg.refresh_token')).toBe('refresh-xyz')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('home'))
    wrapper.unmount()
  })

  it('注册成功且带邀请码：归一大写后随请求提交', async () => {
    mockedRegister.mockResolvedValue(makePair())
    await router.push({ name: 'register', query: { code: 'ab2d3f5h' } })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123456')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedRegister).toHaveBeenCalledWith({
        name: '张三',
        pin: '123456',
        code: 'AB2D3F5H',
      }),
    )
    wrapper.unmount()
  })

  it('本地校验：两次 PIN 不一致直接提示，不调用 API', async () => {
    await router.push({ name: 'register' })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123457')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    expect(wrapper.find('[data-test="register-error"]').text()).toBe('两次输入的 PIN 码不一致')
    expect(mockedRegister).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('本地校验：邀请码格式非法（含混淆字符/位数不足）直接提示', async () => {
    await router.push({ name: 'register' })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123456')
    await wrapper.find('[data-test="register-code"] input').setValue('O1IABC')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    expect(wrapper.find('[data-test="register-code-error"]').text()).toContain('8-10 位')
    expect(mockedRegister).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('用户名占用：防枚举统一文案原样呈现（error-handling.md）', async () => {
    mockedRegister.mockRejectedValue(
      new ApiError(409, 'USERNAME_ALREADY_REGISTERED', '该用户名已被注册'),
    )
    await router.push({ name: 'register' })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123456')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="register-error"]').text()).toBe('该用户名已被注册'),
    )
    wrapper.unmount()
  })

  it('码校验失败：字段级文案呈现在邀请码输入下方，不混入表单级错误', async () => {
    mockedRegister.mockRejectedValue(new ApiError(400, 'INVITE_CODE_INVALID', '邀请码已过期'))
    await router.push({ name: 'register', query: { code: 'AB2D3F5H' } })
    const wrapper = await mountRegister(router, pinia)

    await wrapper.find('[data-test="register-name"] input').setValue('张三')
    await wrapper.find('[data-test="register-pin"] input').setValue('123456')
    await wrapper.find('[data-test="register-pin-confirm"] input').setValue('123456')
    await wrapper.find('[data-test="register-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="register-code-error"]').text()).toBe('邀请码已过期'),
    )
    expect(wrapper.find('[data-test="register-error"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('OnboardingView（冷启动注册引导，决策 17）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    document.body.innerHTML = ''
  })

  async function mountOnboarding(pinia: Pinia): Promise<ReturnType<typeof mount>> {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/login', name: 'login', component: { template: '<div />' } },
        { path: '/register', name: 'register', component: { template: '<div />' } },
        { path: '/onboarding', name: 'onboarding', component: OnboardingView },
      ],
    })
    await router.push({ name: 'onboarding' })
    await router.isReady()
    const wrapper = mount(OnboardingView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    return wrapper
  }

  it('开关开：零账号文案引导注册并提供 /register 按钮，保留登录入口', async () => {
    const wrapper = await mountOnboarding(createPinia())

    expect(wrapper.find('[data-test="onboarding-to-register"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="onboarding-desc"]').text()).toContain('注册')
    expect(wrapper.find('[data-test="onboarding-to-login"]').exists()).toBe(true)
    // 静态页性质：不暴露任何产品面信息
    expect(wrapper.text()).not.toContain('系统管理员')
    wrapper.unmount()
  })

  it('开关关（registration_enabled=false）：注册入口隐藏，仅保留登录指引', async () => {
    const pinia = createPinia()
    const auth = useAuthStore(pinia)
    auth.registrationEnabled = false
    const wrapper = await mountOnboarding(pinia)

    expect(wrapper.find('[data-test="onboarding-to-register"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="onboarding-to-login"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
