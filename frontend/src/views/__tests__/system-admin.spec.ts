import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as adminApi from '@/api/admin'
import SystemAdminView from '@/views/SystemAdminView.vue'
import type { SpaceManagerApplication } from '@/types/api'

vi.mock('@/api/admin', () => ({
  fetchAdminAccounts: vi.fn().mockResolvedValue([]),
  fetchAdminSpaces: vi.fn().mockResolvedValue([]),
  fetchAdminSpaceManagers: vi.fn().mockResolvedValue([]),
  fetchAdminSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchAdminTransferConsents: vi.fn().mockResolvedValue([]),
  fetchManagerApplications: vi.fn().mockResolvedValue([]),
  decideManagerApplication: vi.fn(),
}))

const mockedSpaces = vi.mocked(adminApi.fetchAdminSpaces)
const mockedSpaceMembers = vi.mocked(adminApi.fetchAdminSpaceMembers)
const mockedConsents = vi.mocked(adminApi.fetchAdminTransferConsents)
const mockedApplications = vi.mocked(adminApi.fetchManagerApplications)
const mockedDecide = vi.mocked(adminApi.decideManagerApplication)

function makeApplication(
  overrides: Partial<SpaceManagerApplication> = {},
): SpaceManagerApplication {
  return {
    id: 51,
    applicant_user_id: 7,
    applicant_name: '李小妹',
    space_id: 3,
    space_name: '王家族谱',
    space_kind: 'lineage',
    current_manager_user_id: 8,
    current_manager_name: '王大伯',
    request_kind: 'space_admin',
    status: 'pending',
    decision_note: null,
    created_at: '2026-08-30T00:00:00',
    decided_at: null,
    ...overrides,
  }
}

function makeConsent(
  overrides: Partial<adminApi.AdminTransferConsentMetadata> = {},
): adminApi.AdminTransferConsentMetadata {
  return {
    id: 77,
    application_id: 51,
    space_id: 3,
    space_name: '王家族谱',
    space_kind: 'lineage',
    applicant_user_id: 7,
    applicant_name: '李小妹',
    current_manager_user_id: 8,
    current_manager_name: '王大伯',
    status: 'pending',
    requested_at: '2026-08-31T00:00:00',
    responded_at: null,
    response_reason: null,
    ...overrides,
  }
}

async function mountView() {
  const wrapper = mount(SystemAdminView)
  await vi.waitFor(() =>
    expect(wrapper.find('[data-test="system-admin-loading"]').exists()).toBe(false),
  )
  return wrapper
}

describe('SystemAdminView（PRD R5 最小元数据后台）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedSpaces.mockResolvedValue([])
    mockedSpaceMembers.mockResolvedValue([])
    mockedConsents.mockResolvedValue([])
    mockedApplications.mockResolvedValue([])
  })

  it('待审批申请：显示申请人、目标空间与现任管理员，approve 后重新拉取', async () => {
    mockedApplications.mockResolvedValue([makeApplication()])
    mockedDecide.mockResolvedValue(makeApplication())
    const wrapper = await mountView()

    const card = wrapper.find('[data-test="application-51"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('李小妹')
    expect(card.text()).toContain('王家族谱')
    expect(card.text()).toContain('王大伯')
    expect(wrapper.find('[data-test="pending-application-count"]').text()).toContain('1')

    await wrapper.find('[data-test="approve-51"]').trigger('click')
    await vi.waitFor(() => expect(mockedDecide).toHaveBeenCalledWith(51, 'approve', undefined))
    // 裁决后刷新：初次加载 1 次 + 裁决后 1 次
    await vi.waitFor(() => expect(mockedApplications).toHaveBeenCalledTimes(2))
    wrapper.unmount()
  })

  it('两阶段审批可见：工单 pending 时申请显式停在「待原管理员同意」', async () => {
    // 第一次 approve 只建工单，申请仍是 pending。运营必须能看出还差一步，
    // 否则会以为点过按钮就换人了。
    mockedApplications.mockResolvedValue([
      makeApplication({ transfer_consent_status: 'pending', transfer_consent_id: 77 }),
    ])
    mockedConsents.mockResolvedValue([makeConsent()])
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="application-stage-51"]').text()).toContain('待原管理员同意')
    expect(wrapper.find('[data-test="consent-77"]').text()).toContain('王大伯')
    expect(wrapper.find('[data-test="pending-consent-count"]').text()).toContain('1')
    wrapper.unmount()
  })

  it('原管理员已同意：阶段提示改为可完成交接', async () => {
    mockedApplications.mockResolvedValue([
      makeApplication({ transfer_consent_status: 'accepted', transfer_consent_id: 77 }),
    ])
    mockedConsents.mockResolvedValue([makeConsent({ status: 'accepted' })])
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="application-stage-51"]').text()).toContain('可完成交接')
    wrapper.unmount()
  })

  it('驳回必须填写理由：为空时不发请求，填写后携带备注', async () => {
    mockedApplications.mockResolvedValue([makeApplication()])
    mockedDecide.mockResolvedValue(makeApplication({ status: 'rejected' }))
    const wrapper = await mountView()

    await wrapper.find('[data-test="reject-51"]').trigger('click')
    expect(mockedDecide).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="system-admin-notice"]').text()).toContain('必须填写理由')

    await wrapper.find('[data-test="decision-note-51"]').setValue('资料不足')
    await wrapper.find('[data-test="reject-51"]').trigger('click')
    await vi.waitFor(() => expect(mockedDecide).toHaveBeenCalledWith(51, 'reject', '资料不足'))
    wrapper.unmount()
  })

  it('空间成员按需展开：只在点击后请求该空间，且只显示角色/状态元数据', async () => {
    mockedSpaces.mockResolvedValue([
      {
        id: 3,
        name: '王家族谱',
        kind: 'lineage',
        status: 'active',
        created_at: '2026-08-01T00:00:00',
        manager_user_id: 8,
        manager_account_id: 4,
        manager_name: '王大伯',
      },
    ])
    mockedSpaceMembers.mockResolvedValue([
      {
        user_id: 8,
        account_id: 4,
        name: '王大伯',
        role: 'space_admin',
        status: 'active',
        created_at: '2026-08-01T00:00:00',
        updated_at: '2026-08-01T00:00:00',
      },
    ])
    const wrapper = await mountView()

    // 默认不批量抓取全站成员构成
    expect(mockedSpaceMembers).not.toHaveBeenCalled()

    await wrapper.find('[data-test="toggle-space-members-3"]').trigger('click')
    await vi.waitFor(() => expect(mockedSpaceMembers).toHaveBeenCalledWith(3))
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="space-members-3"]').text()).toContain('空间管理员'),
    )

    // 再次点击收起
    await wrapper.find('[data-test="toggle-space-members-3"]').trigger('click')
    expect(wrapper.find('[data-test="space-members-3"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('加载失败：显示错误且不渲染审批区（fail-closed）', async () => {
    mockedApplications.mockRejectedValue(new Error('network down'))
    const wrapper = mount(SystemAdminView)
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="system-admin-error"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="application-51"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
