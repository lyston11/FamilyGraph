import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
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
  // setup store 的 ref 可直接赋值（先取 store 再注入，避免 state 替换不生效）
  auth.user = {
    id: 1,
    name: '张三',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const wrapper = mount(MessageProvidedSettings, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
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

  it('披露矩阵渲染全部类别；高敏感类别开关禁用；保存提交基础五类', async () => {
    const wrapper = await mountSettings(pinia)

    // n-data-table：tbody 行数 = 10 个披露类别
    const rows = wrapper.findAll('[data-test="disclosure-table"] tbody tr')
    expect(rows.length).toBe(10)

    // 高敏感类别（health 等）禁用
    expect(wrapper.find('[data-test="disclosure-switch-disabled"]').exists()).toBe(true)
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
      }),
    )
    wrapper.unmount()
  })

  it('逐空间披露：基础五类可切换并携带 space_id 保存；高敏感恒禁用', async () => {
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
    const wrapper = await mountSettings(pinia)

    // 矩阵同步后，空间列基础类别开关可用且反映已保存值（dates=true）
    const spaceDates = wrapper.find('[data-test="disclosure-space-7-dates"]')
    expect(spaceDates.exists()).toBe(true)
    await vi.waitFor(() => expect(spaceDates.attributes('aria-checked')).toBe('true'))

    // 高敏感单元格禁用（独立 data-test 哨兵）
    expect(wrapper.find('[data-test="disclosure-space-disabled-7"]').exists()).toBe(true)

    // 在该空间开放 avatar（全局未开）→ 保存时携带 space_id=7
    await wrapper.find('[data-test="disclosure-space-7-avatar"]').trigger('click')
    mockedUpdateDisclosure.mockResolvedValue(makeSelfMember())
    await wrapper.find('[data-test="disclosure-save"]').trigger('click')

    await vi.waitFor(() =>
      expect(mockedUpdateDisclosure).toHaveBeenCalledWith(
        1,
        { avatar: true, photos: false, dates: true, bio: false, attachments: false },
        7,
      ),
    )
    wrapper.unmount()
  })

  it('主题切换：双预览卡点击切主题，aria-pressed 与 data-theme / localStorage 同步', async () => {
    const wrapper = await mountSettings(pinia)

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
    const wrapper = await mountSettings(pinia)

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
    const wrapper = await mountSettings(pinia)

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
