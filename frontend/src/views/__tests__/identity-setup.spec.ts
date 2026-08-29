import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as governanceApi from '@/api/governance'
import IdentitySetupView from '@/views/IdentitySetupView.vue'
import type { FactReview } from '@/types/api'

vi.mock('@/api/governance', () => ({
  confirmIdentity: vi.fn(),
  fetchFactReviews: vi.fn(),
  decideFactReview: vi.fn(),
  fetchDataRights: vi.fn(),
  requestExport: vi.fn(),
  requestCorrection: vi.fn(),
  requestDeletion: vi.fn(),
  executeDelete: vi.fn(),
  downloadExport: vi.fn(),
  raiseClaimDispute: vi.fn(),
  fetchMyClaimDisputes: vi.fn(),
  withdrawClaimDispute: vi.fn(),
}))

const mockedConfirm = vi.mocked(governanceApi.confirmIdentity)
const mockedFetchReviews = vi.mocked(governanceApi.fetchFactReviews)
const mockedDecide = vi.mocked(governanceApi.decideFactReview)

function makeReview(overrides: Partial<FactReview> = {}): FactReview {
  return {
    id: 1,
    item_type: 'name',
    item_ref_json: { field: 'name', value: '张三' },
    status: 'proposed',
    decided_at: null,
    created_at: '2026-08-26T00:00:00',
    ...overrides,
  }
}

// n-modal 内容 teleport 到 body
const dialogInBody = (): Element | null =>
  document.querySelector('[data-test="dispute-dialog"]')

// 单根包裹组件：naive useMessage 需 NMessageProvider 祖先；div 根保证 test-utils 元素查询稳定
const MessageProvidedSetup = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(IdentitySetupView))])
  },
})

async function mountView(
  userOverrides: Partial<{ profile_status: string }> = {},
): Promise<{ wrapper: ReturnType<typeof mount>; router: ReturnType<typeof createRouter> }> {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/identity-setup', name: 'identity-setup', component: IdentitySetupView },
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/stats', name: 'stats', component: { template: '<div />' } },
      { path: '/:pathMatch(.*)*', redirect: '/' },
    ],
  })
  router.push('/identity-setup')
  await router.isReady()
  // 模拟已登录（守卫已在真实链路完成；profile_status 为 /me 直出判定源）
  pinia.state.value.auth = {
    user: {
      id: 1,
      name: '张三',
      is_admin: false,
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'provisional',
      ...userOverrides,
    },
  }
  const wrapper = mount(MessageProvidedSetup, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, router }
}

describe('IdentitySetupView（v2 F-1 确档向导）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('第一步「这是我」：确认成功后进入清单步骤', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview()])
    mockedConfirm.mockResolvedValue({ account_claimed: true, profile_confirmed: true })
    const { wrapper, router } = await mountView()
    void router

    expect(wrapper.find('[data-test="confirm-step"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="checklist-step"]').exists()).toBe(false)

    await wrapper.find('[data-test="confirm-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="checklist-step"]').exists()).toBe(true),
    )
    wrapper.unmount()
  })

  it('此前已确认过（后端 409）：视为通过，直接进入清单', async () => {
    const { ApiError } = await import('@/api/errors')
    mockedFetchReviews.mockResolvedValue([makeReview()])
    mockedConfirm.mockRejectedValue(new ApiError(409, 'IDENTITY_INVALID_TRANSITION', '身份已确认，无需重复操作'))
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="confirm-btn"]').trigger('click')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="checklist-step"]')).toBeTruthy(),
    )
    wrapper.unmount()
  })

  it('profile_status=identity_confirmed：直接进入清单步骤，不再重复「这是我」（Gap2）', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview({ status: 'confirmed' })])
    const { wrapper } = await mountView({ profile_status: 'identity_confirmed' })

    expect(mockedConfirm).not.toHaveBeenCalled()
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="checklist-step"]').exists()).toBe(true),
    )
    wrapper.unmount()
  })

  it('清单逐项确认：全部决议后出现完成按钮并返回主界面', async () => {
    mockedFetchReviews.mockResolvedValue([
      makeReview(),
      makeReview({
        id: 2,
        item_type: 'relation_to_creator',
        item_ref_json: { creator_id: 5, creator_name: '王建国' },
      }),
    ])
    mockedConfirm.mockResolvedValue({ account_claimed: true, profile_confirmed: true })
    mockedDecide.mockImplementation((id, decision) =>
      Promise.resolve(
        makeReview({
          id,
          status: decision === 'confirmed' ? 'confirmed' : 'disputed',
          decided_at: '2026-08-26T01:00:00',
        }),
      ),
    )
    const { wrapper, router } = await mountView()

    await wrapper.find('[data-test="confirm-btn"]').trigger('click')
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="review-item-1"]').exists()).toBe(true),
    )
    // 关系条目展示创建者名
    expect(wrapper.find('[data-test="review-item-2"]').text()).toContain('王建国')

    await wrapper.find('[data-test="review-confirm-1"]').trigger('click')
    await vi.waitFor(() => expect(mockedDecide).toHaveBeenCalledWith(1, 'confirmed', null))
    await wrapper.find('[data-test="review-confirm-2"]').trigger('click')

    await vi.waitFor(() => expect(wrapper.find('[data-test="finish-btn"]').exists()).toBe(true))
    await wrapper.find('[data-test="finish-btn"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    expect(router.currentRoute.value.path).toBe('/')
    wrapper.unmount()
  })

  it('完成确档后回到安全的原始 redirect', async () => {
    mockedFetchReviews.mockResolvedValue([])
    const { wrapper, router } = await mountView({ profile_status: 'identity_confirmed' })
    await router.replace({ name: 'identity-setup', query: { redirect: '/stats' } })
    await wrapper.vm.$nextTick()

    await vi.waitFor(() => expect(wrapper.find('[data-test="finish-btn"]').exists()).toBe(true))
    await wrapper.find('[data-test="finish-btn"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('stats'))
    wrapper.unmount()
  })

  it('争议流程：填写备注提交 disputed 决议', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview()])
    mockedConfirm.mockResolvedValue({ account_claimed: true, profile_confirmed: true })
    mockedDecide.mockResolvedValue(makeReview({ status: 'disputed' }))
    const { wrapper } = await mountView()

    await wrapper.find('[data-test="confirm-btn"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('[data-test="review-dispute-1"]').exists()).toBe(true))

    await wrapper.find('[data-test="review-dispute-1"]').trigger('click')
    await vi.waitFor(() => expect(dialogInBody()).not.toBeNull())

    // data-test 经 input-props 落在原生 textarea 上
    const note = document.querySelector<HTMLTextAreaElement>('[data-test="dispute-note-input"]')!
    note.value = '这个名字不是我的'
    note.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    ;(document.querySelector('[data-test="dispute-submit"]') as HTMLButtonElement).click()

    await vi.waitFor(() =>
      expect(mockedDecide).toHaveBeenCalledWith(1, 'disputed', '这个名字不是我的'),
    )
    wrapper.unmount()
  })
})
