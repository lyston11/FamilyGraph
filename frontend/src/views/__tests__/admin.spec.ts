import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NDialogProvider, NMessageProvider } from 'naive-ui'

import * as adminApi from '@/api/admin'
import AdminView from '@/views/AdminView.vue'
import type {
  DataRightRequest,
  OwnerInvitation,
  OwnerInvitationCreated,
  SpaceManagerApplication,
} from '@/types/api'

vi.mock('@/api/admin', () => ({
  fetchAdminUsers: vi.fn().mockResolvedValue([]),
  fetchAuditLogs: vi.fn().mockResolvedValue([]),
  adminResetPin: vi.fn(),
  createOwnerInvitation: vi.fn(),
  fetchOwnerInvitations: vi.fn().mockResolvedValue([]),
  revokeOwnerInvitation: vi.fn(),
  fetchManagerApplications: vi.fn().mockResolvedValue([]),
  decideManagerApplication: vi.fn(),
  fetchAdminDataRights: vi.fn().mockResolvedValue([]),
  resolveCorrection: vi.fn(),
  fetchAdminClaimDisputes: vi.fn().mockResolvedValue([]),
  resolveClaimDispute: vi.fn(),
}))

const mockedCreateInvitation = vi.mocked(adminApi.createOwnerInvitation)
const mockedFetchManagerApps = vi.mocked(adminApi.fetchManagerApplications)
const mockedDecideManagerApp = vi.mocked(adminApi.decideManagerApplication)
const mockedFetchRights = vi.mocked(adminApi.fetchAdminDataRights)
const mockedResolveCorrection = vi.mocked(adminApi.resolveCorrection)
const mockedFetchDisputes = vi.mocked(adminApi.fetchAdminClaimDisputes)
const mockedResolveDispute = vi.mocked(adminApi.resolveClaimDispute)

function makeManagerApplication(
  overrides: Partial<SpaceManagerApplication> = {},
): SpaceManagerApplication {
  return {
    id: 51,
    applicant_user_id: 7,
    applicant_name: '申请人',
    space_id: 3,
    space_name: '大家族',
    request_kind: 'space_admin',
    status: 'pending',
    decision_note: null,
    created_at: '2026-08-30T00:00:00',
    decided_at: null,
    ...overrides,
  }
}

function makeInvitation(overrides: Partial<OwnerInvitation> = {}): OwnerInvitation {
  return {
    id: 1,
    expires_at: '2099-08-27T00:00:00Z',
    used_at: null,
    revoked_at: null,
    created_at: '2026-08-26T00:00:00',
    ...overrides,
  }
}

function makeCorrection(overrides: Partial<DataRightRequest> = {}): DataRightRequest {
  return {
    id: 21,
    type: 'correct',
    status: 'pending',
    scope: 'self',
    policy_version: 'v2',
    payload_json: { fields: { name: '新名字' } },
    expires_at: null,
    created_at: '2026-08-26T00:00:00',
    finished_at: null,
    ...overrides,
  }
}

// AdminView 迁 naive-ui（P5）：useMessage/useDialog 需 provider 祖先；
// n-modal teleport 到 body，弹层断言走 document 查询；div 根保证组件树查询稳定
const ProvidedAdmin = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(NDialogProvider, () => h(AdminView)))])
  },
})

async function mountAdmin() {
  const pinia = createPinia()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/admin', name: 'admin', component: AdminView },
    ],
  })
  await router.push('/admin')
  await router.isReady()
  const wrapper = mount(ProvidedAdmin, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, router }
}

describe('AdminView（v2 平台运营者语义）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('管理后台返回按钮使用 family-space 命名路由', async () => {
    const { wrapper, router } = await mountAdmin()

    await wrapper.find('[data-test="admin-back"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('family-space'))
    wrapper.unmount()
  })

  it('签发 owner 邀请：一次性 token 仅弹窗展示一次，列表出现有效记录', async () => {
    const created: OwnerInvitationCreated = { ...makeInvitation(), token: 'tok-secret-123' }
    mockedCreateInvitation.mockResolvedValue(created)
    vi.mocked(adminApi.fetchOwnerInvitations).mockResolvedValue([makeInvitation()])
    const { wrapper } = await mountAdmin()

    expect(wrapper.find('[data-test="operator-scope-hint"]').exists()).toBe(true)

    await wrapper.find('[data-test="issue-invitation"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="issued-token"]')?.textContent).toContain(
        'tok-secret-123',
      ),
    )
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="invitation-table"]').text()).toContain('有效'),
    )
    // 关闭路径：点 n-card 头部关闭钮 → update:show=false → issuedToken 清空。
    // jsdom 下 n-modal 离场过渡不结束、内容节点滞留 DOM，故卸载后再断言
    // （与 OneTimePinDialog.spec「卸载后 PIN 不应再出现」同一约定）
    const closeButton = document.querySelector<HTMLElement>('.n-card-header__close')!
    closeButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))
    wrapper.unmount()
    await new Promise((resolve) => setTimeout(resolve))
    expect(document.querySelector('[data-test="issued-token"]')).toBeNull()
  })

  it('更正决议（break-glass）：理由为空时提交禁用；填写后调用 resolveCorrection', async () => {
    mockedFetchRights.mockResolvedValue([makeCorrection()])
    mockedResolveCorrection.mockResolvedValue(makeCorrection({ status: 'completed' }))
    const { wrapper } = await mountAdmin()

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="resolve-correction-21"]').exists()).toBe(true),
    )
    await wrapper.find('[data-test="resolve-correction-21"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="correction-dialog"]')).not.toBeNull(),
    )

    const submit = (): HTMLButtonElement =>
      document.querySelector<HTMLButtonElement>('[data-test="correction-submit"]')!
    expect(submit().disabled).toBe(true)

    const note = document.querySelector<HTMLTextAreaElement>('[data-test="correction-note-input"]')!
    note.value = '经核实与原始证据一致，批准更正'
    note.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    expect(submit().disabled).toBe(false)
    submit().click()

    await vi.waitFor(() =>
      expect(mockedResolveCorrection).toHaveBeenCalledWith(21, true, '经核实与原始证据一致，批准更正'),
    )
    wrapper.unmount()
  })

  it('争议决议（break-glass）：选择结论 + 必填理由后调用 resolveClaimDispute', async () => {
    mockedFetchDisputes.mockResolvedValue([
      {
        id: 31,
        profile_id: 5,
        raised_by_account_id: 9,
        status: 'open',
        created_at: '2026-08-26T00:00:00',
        resolved_at: null,
        resolution_note: null,
      },
    ])
    mockedResolveDispute.mockResolvedValue({ id: 31, status: 'resolved_reject', resolution_note: 'x' })
    const { wrapper } = await mountAdmin()

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="resolve-dispute-31"]').exists()).toBe(true),
    )
    await wrapper.find('[data-test="resolve-dispute-31"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="dispute-resolve-dialog"]')).not.toBeNull(),
    )

    // 选择「驳回认领」：n-radio 渲染原生 input，走原生 click
    const radios = document.querySelectorAll<HTMLInputElement>(
      '[data-test="dispute-outcome-group"] input[type="radio"]',
    )
    expect(radios.length).toBe(2)
    radios[1].click()
    await new Promise((resolve) => setTimeout(resolve))

    const note = document.querySelector<HTMLTextAreaElement>('[data-test="dispute-note-input"]')!
    note.value = '证据不足，驳回认领'
    note.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    ;(document.querySelector('[data-test="dispute-resolution-submit"]') as HTMLButtonElement).click()

    await vi.waitFor(() =>
      expect(mockedResolveDispute).toHaveBeenCalledWith(31, 'resolved_reject', '证据不足，驳回认领'),
    )
    wrapper.unmount()
  })

  it('空间管理者申请：approve 行内直接裁决并刷新列表', async () => {
    mockedFetchManagerApps.mockResolvedValue([makeManagerApplication()])
    mockedDecideManagerApp.mockResolvedValue(makeManagerApplication({ status: 'approved' }))
    const { wrapper } = await mountAdmin()

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="approve-application-51"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="manager-application-table"]').text()).toContain('申请成为空间管理员')
    expect(wrapper.find('[data-test="manager-application-table"]').text()).toContain('大家族')

    await wrapper.find('[data-test="approve-application-51"]').trigger('click')
    await vi.waitFor(() =>
      expect(mockedDecideManagerApp).toHaveBeenCalledWith(51, 'approve'),
    )
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('已通过并留痕审计'),
    )
    wrapper.unmount()
  })

  it('空间管理者申请：驳回必须填写理由（为空禁用），填写后携带备注裁决', async () => {
    mockedFetchManagerApps.mockResolvedValue([
      makeManagerApplication({ request_kind: 'space_admin', space_id: 3, space_name: '大家族' }),
    ])
    mockedDecideManagerApp.mockResolvedValue(makeManagerApplication({ status: 'rejected' }))
    const { wrapper } = await mountAdmin()

    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="reject-application-51"]').exists()).toBe(true),
    )
    await wrapper.find('[data-test="reject-application-51"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="manager-reject-dialog"]')).not.toBeNull(),
    )

    const submit = (): HTMLButtonElement =>
      document.querySelector<HTMLButtonElement>('[data-test="manager-reject-submit"]')!
    expect(submit().disabled).toBe(true)

    const note = document.querySelector<HTMLTextAreaElement>('[data-test="manager-note-input"]')!
    note.value = '目标空间成员关系已变动，请重新申请'
    note.dispatchEvent(new Event('input'))
    await new Promise((resolve) => setTimeout(resolve))
    expect(submit().disabled).toBe(false)
    submit().click()

    await vi.waitFor(() =>
      expect(mockedDecideManagerApp).toHaveBeenCalledWith(
        51,
        'reject',
        '目标空间成员关系已变动，请重新申请',
      ),
    )
    wrapper.unmount()
  })
})
