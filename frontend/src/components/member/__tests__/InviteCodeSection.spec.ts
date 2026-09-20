import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import { ApiError } from '@/api/errors'
import * as bindingsApi from '@/api/bindings'
import * as inviteCodesApi from '@/api/inviteCodes'
import * as spacesApi from '@/api/spaces'
import InviteCodeSection from '@/components/member/InviteCodeSection.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { Binding, FamilySpace, InviteCode } from '@/types/api'

/**
 * 09-05 Chunk E 前端测试：设置页「邀请码」区块——
 * 码列表渲染/撤销/复制链接、创建三类码（含空间选择与陌生人码上限）、
 * 填码加入（后端文案原样呈现）、绑定确认（PIN 复验）；
 * 决策 13 修订：provisional 用户建码表单可见可用，无空间时只能建陌生人码。
 */

vi.mock('@/api/inviteCodes', () => ({
  fetchMyInviteCodes: vi.fn(),
  createInviteCode: vi.fn(),
  revokeInviteCode: vi.fn(),
  redeemInviteCode: vi.fn(),
}))

vi.mock('@/api/bindings', () => ({
  fetchMyBindings: vi.fn(),
  confirmBinding: vi.fn(),
  rejectBinding: vi.fn(),
  cancelBinding: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
}))

const mockedFetchCodes = vi.mocked(inviteCodesApi.fetchMyInviteCodes)
const mockedCreateCode = vi.mocked(inviteCodesApi.createInviteCode)
const mockedRevokeCode = vi.mocked(inviteCodesApi.revokeInviteCode)
const mockedRedeem = vi.mocked(inviteCodesApi.redeemInviteCode)
const mockedFetchBindings = vi.mocked(bindingsApi.fetchMyBindings)
const mockedConfirmBinding = vi.mocked(bindingsApi.confirmBinding)
const mockedRejectBinding = vi.mocked(bindingsApi.rejectBinding)
const mockedFetchSpaces = vi.mocked(spacesApi.fetchSpaces)

function makeCode(overrides: Partial<InviteCode> = {}): InviteCode {
  return {
    id: 1,
    code: 'AB2D3F5H',
    kind: 'household',
    space_id: 7,
    space_name: '我的家庭',
    max_uses: 1,
    used_count: 0,
    expires_at: '2099-01-01T00:00:00',
    revoked_at: null,
    created_at: '2026-09-05T00:00:00',
    ...overrides,
  }
}

function makeBinding(overrides: Partial<Binding> = {}): Binding {
  return {
    id: 100,
    initiator_name: '王大壮',
    person_name: '王小壮',
    status: 'pending',
    created_at: '2026-09-05T00:00:00',
    resolved_at: null,
    ...overrides,
  }
}

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 7,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-09-01T00:00:00',
    pending_count: 0,
    member_count: 1,
    ...overrides,
  }
}

const MessageProvided = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(InviteCodeSection))])
  },
})

async function mountSection(
  profileStatus: 'provisional' | 'identity_confirmed' = 'identity_confirmed',
): Promise<{ wrapper: ReturnType<typeof mount>; pinia: Pinia }> {
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '张三',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: profileStatus,
  }
  const wrapper = mount(MessageProvided, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, pinia }
}

/** n-select 键盘路径（SpaceGovernanceDialog.spec 同款）：jsdom 下选项 DOM 不渲染 */
async function pickFirstOption(wrapper: ReturnType<typeof mount>): Promise<void> {
  const selection = wrapper.find('[data-test="invite-create-space"] .n-base-selection')
  selection.trigger('keydown', { key: 'Enter' })
  await vi.waitFor(() => expect(document.querySelector('.n-base-select-menu')).not.toBeNull())
  selection.trigger('keydown', { key: 'Enter' })
  await flushPromises()
}

describe('InviteCodeSection：我的码列表', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
    mockedFetchBindings.mockResolvedValue([])
    mockedFetchSpaces.mockResolvedValue([])
  })

  it('渲染我的码：kind 徽标、使用次数、状态；已撤销码不显示撤销按钮', async () => {
    mockedFetchCodes.mockResolvedValue([
      makeCode(),
      makeCode({
        id: 2,
        code: 'QR2T6789',
        kind: 'stranger',
        space_id: null,
        space_name: null,
        max_uses: null,
        used_count: 3,
        revoked_at: '2026-09-04T00:00:00',
      }),
    ])
    const { wrapper } = await mountSection()

    expect(wrapper.find('[data-test="invite-codes-table"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="invite-code-kind-1"]').text()).toBe('家庭码')
    expect(wrapper.find('[data-test="invite-code-kind-2"]').text()).toBe('陌生人码')
    expect(wrapper.find('[data-test="invite-code-status-1"]').text()).toBe('有效')
    expect(wrapper.find('[data-test="invite-code-status-2"]').text()).toBe('已撤销')
    // 陌生人码不限次：已用计数文案
    expect(wrapper.text()).toContain('已用 3 次')
    expect(wrapper.find('[data-test="invite-code-revoke-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="invite-code-revoke-2"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('撤销：调用 revokeInviteCode 并以响应行更新状态', async () => {
    mockedFetchCodes.mockResolvedValue([makeCode()])
    mockedRevokeCode.mockResolvedValue(makeCode({ revoked_at: '2026-09-05T01:00:00' }))
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="invite-code-revoke-1"]').trigger('click')
    await flushPromises()

    expect(mockedRevokeCode).toHaveBeenCalledWith(1)
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="invite-code-status-1"]').text()).toBe('已撤销'),
    )
    wrapper.unmount()
  })

  it('复制分享链接：剪贴板收到 /register?code=XXX（决策 12）', async () => {
    mockedFetchCodes.mockResolvedValue([makeCode()])
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="invite-code-copy-1"]').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledTimes(1)
    const link = writeText.mock.calls[0]?.[0] ?? ''
    expect(link).toContain('/register?code=AB2D3F5H')
    wrapper.unmount()
  })
})

describe('InviteCodeSection：创建三类码', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
    mockedFetchCodes.mockResolvedValue([])
    mockedFetchBindings.mockResolvedValue([])
  })

  it('家庭码必须先选择所在空间：未选时本地提示，不调用 API', async () => {
    mockedFetchSpaces.mockResolvedValue([makeSpace()])
    const { wrapper } = await mountSection()
    await flushPromises()

    await wrapper.find('[data-test="invite-create-submit"]').trigger('click')

    expect(wrapper.find('[data-test="invite-create-error"]').text()).toBe('请选择邀请码所属的空间')
    expect(mockedCreateCode).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('选择空间后创建家庭码：载荷带 space_id，新码置顶', async () => {
    mockedFetchSpaces.mockResolvedValue([makeSpace({ id: 7, kind: 'household' })])
    mockedCreateCode.mockResolvedValue(makeCode({ id: 9, code: 'NEW2CODE' }))
    const { wrapper } = await mountSection()
    await flushPromises()

    await pickFirstOption(wrapper)
    await wrapper.find('[data-test="invite-create-submit"]').trigger('click')
    await flushPromises()

    expect(mockedCreateCode).toHaveBeenCalledWith({
      kind: 'household',
      space_id: 7,
      max_uses: null,
      ttl_days: 7,
    })
    expect(wrapper.find('[data-test="invite-code-row-9"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('陌生人码：不选空间、可设使用上限', async () => {
    mockedCreateCode.mockResolvedValue(
      makeCode({ id: 10, code: 'STR2CODE', kind: 'stranger', space_id: null, space_name: null, max_uses: 5 }),
    )
    const { wrapper } = await mountSection()

    // 切换类型为陌生人码（n-radio 根元素点击）
    const radios = wrapper.findAll('[data-test="invite-create-kind"] .n-radio')
    const strangerRadio = radios.find((radio) => radio.text().includes('陌生人码'))
    expect(strangerRadio).toBeDefined()
    await strangerRadio!.trigger('click')
    await flushPromises()

    // 使用上限输入：blur 提交（n-input-number 默认 update-value-on-input=false）
    const usesInput = wrapper.find('[data-test="invite-create-max-uses"] input')
    await usesInput.setValue('5')
    await usesInput.trigger('blur')
    await flushPromises()

    await wrapper.find('[data-test="invite-create-submit"]').trigger('click')
    await flushPromises()

    expect(mockedCreateCode).toHaveBeenCalledWith({
      kind: 'stranger',
      space_id: null,
      max_uses: 5,
      ttl_days: 7,
    })
    wrapper.unmount()
  })

  it('provisional 用户：建码表单可见可用，空间列表正常预取（决策 13 修订）', async () => {
    mockedFetchCodes.mockResolvedValue([])
    mockedFetchBindings.mockResolvedValue([])
    mockedFetchSpaces.mockResolvedValue([makeSpace()])
    const { wrapper } = await mountSection('provisional')
    await flushPromises()

    expect(wrapper.find('[data-test="invite-create-disabled"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="invite-create-submit"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="invite-create-space"]').exists()).toBe(true)
    expect(mockedFetchSpaces).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('无空间时：家庭/家族码空间选择器为空并提示先建空间，陌生人码仍可直接创建', async () => {
    mockedFetchSpaces.mockResolvedValue([])
    const { wrapper } = await mountSection()
    await flushPromises()

    // 默认 household：空间选择器无选项，展示中性提示（不涉身份确认）
    expect(wrapper.find('[data-test="invite-create-no-spaces"]').text()).toBe(
      '创建空间后可生成家庭/家族邀请码',
    )

    // 切到陌生人码：提示消失，可不选空间直接创建
    const radios = wrapper.findAll('[data-test="invite-create-kind"] .n-radio')
    const strangerRadio = radios.find((radio) => radio.text().includes('陌生人码'))
    expect(strangerRadio).toBeDefined()
    await strangerRadio!.trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="invite-create-no-spaces"]').exists()).toBe(false)

    mockedCreateCode.mockResolvedValue(
      makeCode({ id: 11, code: 'LON2CODE', kind: 'stranger', space_id: null, space_name: null }),
    )
    await wrapper.find('[data-test="invite-create-submit"]').trigger('click')
    await flushPromises()
    expect(mockedCreateCode).toHaveBeenCalledWith({
      kind: 'stranger',
      space_id: null,
      max_uses: null,
      ttl_days: 7,
    })
    wrapper.unmount()
  })
})

describe('InviteCodeSection：填码加入', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
    mockedFetchCodes.mockResolvedValue([])
    mockedFetchBindings.mockResolvedValue([])
    mockedFetchSpaces.mockResolvedValue([])
  })

  it('兑换成功：小写输入归一大写，提示加入的空间并刷新空间缓存', async () => {
    mockedRedeem.mockResolvedValue(makeCode({ used_count: 1 }))
    const { wrapper, pinia } = await mountSection()

    const spaces = useSpacesStore(pinia)
    const loadSpy = vi.spyOn(spaces, 'load').mockResolvedValue(undefined)

    await wrapper.find('[data-test="invite-redeem-input"] input').setValue('ab2d3f5h')
    await wrapper.find('[data-test="invite-redeem-relation-label"] input').setValue('堂弟')
    await wrapper.find('[data-test="invite-redeem-submit"]').trigger('click')
    await flushPromises()

    // 09-20：兑换必须带关系词；只产生待房主批准的申请
    expect(mockedRedeem).toHaveBeenCalledWith('AB2D3F5H', '堂弟')
    expect(loadSpy).toHaveBeenCalled()
    const input = wrapper.find('[data-test="invite-redeem-input"] input').element as HTMLInputElement
    expect(input.value).toBe('')
    wrapper.unmount()
  })

  it('后端拒绝文案原样呈现（陌生人码仅注册场景 / 已是成员 409）', async () => {
    mockedRedeem.mockRejectedValue(
      new ApiError(400, 'INVITE_CODE_STRANGER_REGISTER_ONLY', '陌生人码仅可在注册时使用'),
    )
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="invite-redeem-input"] input').setValue('AB2D3F5H')
    await wrapper.find('[data-test="invite-redeem-relation-label"] input').setValue('堂弟')
    await wrapper.find('[data-test="invite-redeem-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="invite-redeem-error"]').text()).toBe('陌生人码仅可在注册时使用')

    mockedRedeem.mockRejectedValue(new ApiError(409, 'VALIDATION_ERROR', '你已经是该空间成员'))
    await wrapper.find('[data-test="invite-redeem-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="invite-redeem-error"]').text()).toBe('你已经是该空间成员')
    wrapper.unmount()
  })
})

describe('InviteCodeSection：待确认绑定（决策 16）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
    mockedFetchCodes.mockResolvedValue([])
    mockedFetchSpaces.mockResolvedValue([])
  })

  it('待确认列表渲染最小字段；已决议历史只读展示', async () => {
    mockedFetchBindings.mockResolvedValue([
      makeBinding(),
      makeBinding({ id: 101, status: 'confirmed', resolved_at: '2026-09-05T02:00:00' }),
    ])
    const { wrapper } = await mountSection()

    expect(wrapper.find('[data-test="binding-item-100"]').text()).toContain('王大壮')
    expect(wrapper.find('[data-test="binding-item-100"]').text()).toContain('王小壮')
    expect(wrapper.find('[data-test="binding-confirm-101"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="bindings-history"]').text()).toContain('已确认')
    wrapper.unmount()
  })

  it('无待确认绑定时展示空态', async () => {
    mockedFetchBindings.mockResolvedValue([])
    const { wrapper } = await mountSection()

    expect(wrapper.find('[data-test="bindings-empty"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('确认：弹窗输入 PIN 复验后调用 confirmBinding 并重载列表', async () => {
    mockedFetchBindings
      .mockResolvedValueOnce([makeBinding()])
      .mockResolvedValueOnce([makeBinding({ id: 100, status: 'confirmed', resolved_at: '2026-09-05T03:00:00' })])
    mockedConfirmBinding.mockResolvedValue(
      makeBinding({ id: 100, status: 'confirmed', resolved_at: '2026-09-05T03:00:00' }),
    )
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="binding-confirm-100"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="binding-confirm-dialog"]')).not.toBeNull()

    const pinInput = document.querySelector<HTMLInputElement>('[data-test="binding-confirm-pin"] input')!
    pinInput.value = '123456'
    pinInput.dispatchEvent(new Event('input'))
    await flushPromises()

    ;(document.querySelector('[data-test="binding-confirm-submit"]') as HTMLButtonElement).click()
    await flushPromises()

    expect(mockedConfirmBinding).toHaveBeenCalledWith(100, '123456')
    // 确认成功后重载绑定列表（第二次 fetchMyBindings）
    expect(mockedFetchBindings).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('PIN 不足 6 位：本地提示，不调用 API', async () => {
    mockedFetchBindings.mockResolvedValue([makeBinding()])
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="binding-confirm-100"]').trigger('click')
    await flushPromises()
    const pinInput = document.querySelector<HTMLInputElement>('[data-test="binding-confirm-pin"] input')!
    pinInput.value = '123'
    pinInput.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('[data-test="binding-confirm-submit"]') as HTMLButtonElement).click()
    await flushPromises()

    expect(document.querySelector('[data-test="binding-confirm-error"]')?.textContent).toContain(
      '6 位数字',
    )
    expect(mockedConfirmBinding).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('PIN 复验失败：登录侧统一文案原样呈现，弹窗保留可重试', async () => {
    mockedFetchBindings.mockResolvedValue([makeBinding()])
    mockedConfirmBinding.mockRejectedValue(
      new ApiError(401, 'AUTH_INVALID_CREDENTIALS', '名字或 PIN 码错误'),
    )
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="binding-confirm-100"]').trigger('click')
    await flushPromises()
    const pinInput = document.querySelector<HTMLInputElement>('[data-test="binding-confirm-pin"] input')!
    pinInput.value = '000000'
    pinInput.dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('[data-test="binding-confirm-submit"]') as HTMLButtonElement).click()
    await flushPromises()

    expect(document.querySelector('[data-test="binding-confirm-error"]')?.textContent).toBe(
      '名字或 PIN 码错误',
    )
    wrapper.unmount()
  })

  it('拒绝：调用 rejectBinding 并重载列表', async () => {
    mockedFetchBindings
      .mockResolvedValueOnce([makeBinding()])
      .mockResolvedValueOnce([makeBinding({ id: 100, status: 'rejected', resolved_at: '2026-09-05T04:00:00' })])
    mockedRejectBinding.mockResolvedValue(
      makeBinding({ id: 100, status: 'rejected', resolved_at: '2026-09-05T04:00:00' }),
    )
    const { wrapper } = await mountSection()

    await wrapper.find('[data-test="binding-reject-100"]').trigger('click')
    await flushPromises()

    expect(mockedRejectBinding).toHaveBeenCalledWith(100)
    expect(mockedFetchBindings).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
