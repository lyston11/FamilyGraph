import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as governanceApi from '@/api/governance'
import * as membersApi from '@/api/members'
import SettingsView from '@/views/SettingsView.vue'
import { useAuthStore } from '@/stores/auth'
import type { DataRightRequest, DisclosureCategory, Member } from '@/types/api'

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

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
  fetchDisclosureMatrix: vi.fn(),
}))

vi.mock('@/api/governance', () => ({
  confirmIdentity: vi.fn(),
  fetchFactReviews: vi.fn().mockResolvedValue([]),
  decideFactReview: vi.fn(),
  fetchDataRights: vi.fn(),
  requestExport: vi.fn(),
  requestCorrection: vi.fn(),
  requestDeletion: vi.fn(),
  executeDelete: vi.fn(),
  downloadExport: vi.fn(),
  raiseClaimDispute: vi.fn(),
  fetchMyClaimDisputes: vi.fn().mockResolvedValue([]),
  withdrawClaimDispute: vi.fn(),
}))

// 设置页挂载的家庭绑定与邀请码区块（09-11 R6：不 mock 会让 jsdom 发起真实
// XHR，产生 AggregateError stderr 噪音）
vi.mock('@/api/bindings', () => ({
  fetchMyBindings: vi.fn().mockResolvedValue([]),
  confirmBinding: vi.fn(),
  rejectBinding: vi.fn(),
  cancelBinding: vi.fn(),
}))

vi.mock('@/api/inviteCodes', () => ({
  fetchMyInviteCodes: vi.fn().mockResolvedValue([]),
  createInviteCode: vi.fn(),
  revokeInviteCode: vi.fn(),
  redeemInviteCode: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedFetchMembers = vi.mocked(membersApi.fetchMembers)
const mockedUpdateDisclosure = vi.mocked(membersApi.updateDisclosure)
const mockedFetchMatrix = vi.mocked(membersApi.fetchDisclosureMatrix)
const mockedRequestExport = vi.mocked(governanceApi.requestExport)
const mockedFetchRights = vi.mocked(governanceApi.fetchDataRights)
const mockedRequestDeletion = vi.mocked(governanceApi.requestDeletion)
const mockedExecuteDelete = vi.mocked(governanceApi.executeDelete)

function makeSelfMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 1,
    name: '张三',
    is_admin: false,
    gender: 'm',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'claimed',
    created_by: null,
    created_at: '2026-08-25T00:00:00',
    clan_disclosure: {
      avatar: false,
      photos: false,
      dates: false,
      bio: false,
      attachments: false,
    },
    permissions: { edit: true, delete: true },
    ...overrides,
  }
}

function makeRight(overrides: Partial<DataRightRequest> = {}): DataRightRequest {
  return {
    id: 10,
    type: 'export',
    status: 'pending',
    scope: 'self',
    policy_version: 'v2',
    payload_json: null,
    expires_at: null,
    created_at: '2026-08-26T00:00:00',
    finished_at: null,
    ...overrides,
  }
}

/** 全 false 披露矩阵（十类；后端 GET /users/{id}/disclosure 合同形状） */
function makeMatrix(overrides?: {
  global?: Partial<Record<DisclosureCategory, boolean>>
  spaces?: { space_id: number; allowed: Partial<Record<DisclosureCategory, boolean>> }[]
}) {
  const allFalse = Object.fromEntries(
    (
      [
        'avatar',
        'photos',
        'dates',
        'bio',
        'attachments',
        'health',
        'address',
        'school',
        'contact',
        'private_notes',
      ] as DisclosureCategory[]
    ).map((c) => [c, false]),
  ) as Record<DisclosureCategory, boolean>
  return {
    global: { ...allFalse, ...overrides?.global },
    spaces: (overrides?.spaces ?? []).map((s) => ({
      space_id: s.space_id,
      allowed: { ...allFalse, ...s.allowed },
    })),
  }
}

// SettingsView 全量迁 naive-ui（P5）：useMessage 需 NMessageProvider 祖先；
// div 根保证查询稳定
const MessageProvidedSettings = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(SettingsView))])
  },
})

async function mountSettings(pinia: Pinia) {
  const auth = useAuthStore(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<div />' } },
      { path: '/family-tree', name: 'family-space', component: { template: '<div />' } },
      { path: '/settings', name: 'settings', component: SettingsView },
      { path: '/memory', name: 'memory', component: { template: '<div />' } },
    ],
  })
  await router.push('/settings')
  await router.isReady()
  // setup store 的 ref 可直接赋值（先取 store 再注入，避免 state 替换不生效）
  auth.user = {
    id: 1,
    name: '张三',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const wrapper = mount(MessageProvidedSettings, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, router }
}

describe('SettingsView（v2：披露偏好 + 我的数据）', () => {
  let pinia: Pinia

  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    localStorage.clear()
    pinia = createPinia()
    mockedFetchMembers.mockResolvedValue([makeSelfMember()])
    mockedFetchRights.mockResolvedValue([])
    mockedFetchMatrix.mockResolvedValue(makeMatrix())
  })

  it('设置页返回按钮回家庭卡（route home；修复文案/目标不一致）', async () => {
    const { wrapper, router } = await mountSettings(pinia)

    await wrapper.find('[data-test="settings-back"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('home'))
    wrapper.unmount()
  })

  it('四分区渲染：个人资料 / 隐私与公示 / 账号与安全 / 显示与无障碍（含主题切换）', async () => {
    const { wrapper } = await mountSettings(pinia)

    expect(wrapper.find('[data-test="settings-section-profile"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="settings-section-privacy"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="settings-section-account"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="settings-section-display"]').exists()).toBe(true)

    // 复用组件挂载：改名 / 披露矩阵 / 数据权利 / 改 PIN / 登出
    expect(wrapper.find('[data-test="current-user"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="name-input"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="disclosure-table"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="request-export-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="logout-btn"]').exists()).toBe(true)
    // 显示与无障碍：paper/modern 双主题卡
    expect(wrapper.find('[data-test="theme-card-paper"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="theme-card-modern"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('空间管理不放进全局设置（由 AppShell 当前空间管理入口承担）', async () => {
    const { wrapper } = await mountSettings(pinia)

    expect(wrapper.find('[data-test="space-management-link"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="account-menu-space-management"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('空间管理')
    wrapper.unmount()
  })

  it('披露矩阵渲染全部类别；高敏感类别可开启（09-05 放开）；保存提交全类别', async () => {
    const { wrapper } = await mountSettings(pinia)

    // n-data-table：tbody 行数 = 10 个披露类别
    const rows = wrapper.findAll('[data-test="disclosure-table"] tbody tr')
    expect(rows.length).toBe(10)

    // 高敏感类别（health 等）开关存在且启用（09-05 放开；未成年人矩阵内另行禁用）
    expect(wrapper.find('[data-test="disclosure-switch-health"]').exists()).toBe(true)
    // 基础类别可切换（n-switch 无原生 input，交互走根元素 click）
    const avatarSwitch = wrapper.find('[data-test="disclosure-switch-avatar"]')
    expect(avatarSwitch.exists()).toBe(true)
    await avatarSwitch.trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    mockedUpdateDisclosure.mockResolvedValue(makeSelfMember())
    await wrapper.find('[data-test="disclosure-save"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedUpdateDisclosure).toHaveBeenCalledWith(1, {
        avatar: true,
        photos: false,
        dates: false,
        bio: false,
        attachments: false,
        health: false,
        address: false,
        school: false,
        contact: false,
        private_notes: false,
      }),
    )
    wrapper.unmount()
  })

  it('逐空间披露：基础五类可切换并携带 space_id 保存；高敏感可开启（09-05 放开）', async () => {
    const spacesApi = await import('@/api/spaces')
    vi.mocked(spacesApi.fetchSpaces).mockResolvedValue([
      {
        id: 7,
        name: '宗族',
        owner_id: 1,
        kind: 'lineage',
        created_at: '2026-08-26T00:00:00',
        pending_count: 0,
        member_count: 2,
      },
    ])
    mockedFetchMatrix.mockResolvedValue(
      makeMatrix({ spaces: [{ space_id: 7, allowed: { dates: true } }] }),
    )
    const { wrapper } = await mountSettings(pinia)

    // 矩阵同步后，空间列基础类别开关可用且反映已保存值（dates=true）
    const spaceDates = wrapper.find('[data-test="disclosure-space-7-dates"]')
    expect(spaceDates.exists()).toBe(true)
    await vi.waitFor(() => expect(spaceDates.attributes('aria-checked')).toBe('true'))

    // 高敏感单元格启用（09-05 放开；开启走强确认 Modal）
    expect(wrapper.find('[data-test="disclosure-space-7-health"]').exists()).toBe(true)

    // 在该空间开放 avatar（全局未开）→ 保存时携带 space_id=7
    await wrapper.find('[data-test="disclosure-space-7-avatar"]').trigger('click')
    mockedUpdateDisclosure.mockResolvedValue(makeSelfMember())
    await wrapper.find('[data-test="disclosure-save"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedUpdateDisclosure).toHaveBeenCalledWith(
        1,
        {
          avatar: true,
          photos: false,
          dates: true,
          bio: false,
          attachments: false,
          health: false,
          address: false,
          school: false,
          contact: false,
          private_notes: false,
        },
        7,
      ),
    )
    wrapper.unmount()
  })

  it('高敏感开启强确认：点击开关先弹 Modal，确认后才写入草稿并随保存提交', async () => {
    const { wrapper } = await mountSettings(pinia)

    // 点击 health 开关（false → true）：先弹强确认，草稿未变
    await wrapper.find('[data-test="disclosure-switch-health"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    const dialog = document.querySelector('[data-test="disclosure-high-risk-confirm"]')
    expect(dialog).not.toBeNull()

    // 确认开启 → 草稿更新 → 保存携带 health: true
    const buttons = Array.from(dialog!.querySelectorAll('button'))
    const positive = buttons.find((button) => button.textContent?.includes('确认开启'))
    expect(positive).toBeDefined()
    positive!.click()
    await new Promise((resolve) => setTimeout(resolve))
    await new Promise((resolve) => setTimeout(resolve))

    mockedUpdateDisclosure.mockResolvedValue(makeSelfMember())
    await wrapper.find('[data-test="disclosure-save"]').trigger('click')
    await vi.waitFor(() => {
      const call = mockedUpdateDisclosure.mock.calls.at(-1)
      expect(call?.[1]).toMatchObject({ health: true })
    })
    wrapper.unmount()
  })

  it('主题切换：双预览卡点击切主题，aria-pressed 与 data-theme / localStorage 同步', async () => {
    const { wrapper } = await mountSettings(pinia)

    const paperCard = wrapper.find('[data-test="theme-card-paper"]')
    const modernCard = wrapper.find('[data-test="theme-card-modern"]')
    // 默认纸墨
    expect(paperCard.attributes('aria-pressed')).toBe('true')
    expect(modernCard.attributes('aria-pressed')).toBe('false')
    expect(document.documentElement.dataset.theme).toBe('paper')

    await modernCard.trigger('click')
    expect(modernCard.attributes('aria-pressed')).toBe('true')
    expect(paperCard.attributes('aria-pressed')).toBe('false')
    expect(document.documentElement.dataset.theme).toBe('modern')
    expect(localStorage.getItem('fg-theme')).toBe('modern')

    // 切回纸墨
    await paperCard.trigger('click')
    expect(document.documentElement.dataset.theme).toBe('paper')
    wrapper.unmount()
  })

  it('我的数据：申请导出后历史列表出现该请求', async () => {
    const created = makeRight()
    mockedRequestExport.mockResolvedValue(created)
    mockedFetchRights.mockResolvedValue([created])
    const { wrapper } = await mountSettings(pinia)

    await wrapper.find('[data-test="request-export-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedRequestExport).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="data-right-history"]').text()).toContain('导出'),
    )
    wrapper.unmount()
  })

  it('删除/注销：名字确认后创建请求并立即执行，随后清空本地会话', async () => {
    mockedRequestDeletion.mockResolvedValue(makeRight({ type: 'delete' }))
    mockedExecuteDelete.mockResolvedValue(undefined)
    const { wrapper } = await mountSettings(pinia)

    await wrapper.find('[data-test="open-delete-dialog"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="delete-request-dialog"]')).not.toBeNull(),
    )

    const submit = (): HTMLButtonElement =>
      document.querySelector<HTMLButtonElement>('[data-test="delete-request-submit"]')!
    expect(submit().disabled).toBe(true)

    const input = document.querySelector<HTMLInputElement>(
      '[data-test="delete-request-confirm-input"]',
    )!
    input.value = '张三'
    input.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    expect(submit().disabled).toBe(false)
    submit().click()

    await vi.waitFor(() => expect(mockedRequestDeletion).toHaveBeenCalled())
    await vi.waitFor(() => expect(mockedExecuteDelete).toHaveBeenCalledWith(10, '张三'))
    // 本地会话清空（敏感缓存清理红线）
    await vi.waitFor(() => expect(useAuthStore(pinia).isLoggedIn).toBe(false))
    wrapper.unmount()
  })
})
