import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as spacesApi from '@/api/spaces'
import SpaceManagerApplicationPanel from '@/components/member/SpaceManagerApplicationPanel.vue'
import type {
  EligibleManagerTarget,
  ManagerTransferConsent,
  SpaceManagerApplication,
} from '@/types/api'

/**
 * 空间管理员申请与交接面板（自旧 HomeView 保全的领域流程，09-01 Phase 3）：
 * - 申请资格/候选目标由服务端裁定，无候选时入口不出现（fail-closed）；
 * - 提交的是所选目标空间，不是当前切换到的空间；
 * - 原管理员工单：谢绝缺理由时前端先拦下；申请状态行带领域状态徽章。
 */

vi.mock('@/api/spaces', () => ({
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchEligibleManagerTargets: vi.fn().mockResolvedValue([]),
  fetchMyTransferConsents: vi.fn().mockResolvedValue([]),
  respondTransferConsent: vi.fn(),
}))

const mockedEligibleTargets = vi.mocked(spacesApi.fetchEligibleManagerTargets)
const mockedSubmit = vi.mocked(spacesApi.submitManagerApplication)
const mockedMyConsents = vi.mocked(spacesApi.fetchMyTransferConsents)
const mockedRespondConsent = vi.mocked(spacesApi.respondTransferConsent)

function makeTarget(overrides: Partial<EligibleManagerTarget> = {}): EligibleManagerTarget {
  return {
    space_id: 42,
    space_name: '王家族谱',
    space_kind: 'lineage',
    current_manager_user_id: 8,
    current_manager_name: '王大伯',
    has_pending_application: false,
    ...overrides,
  }
}

function makeApplication(overrides: Partial<SpaceManagerApplication> = {}): SpaceManagerApplication {
  return {
    id: 11,
    applicant_user_id: 5,
    applicant_name: '空间用户',
    space_id: 1,
    space_name: '我们家',
    request_kind: 'space_admin',
    status: 'pending',
    decision_note: null,
    created_at: '2026-08-30T00:00:00',
    decided_at: null,
    ...overrides,
  }
}

function makeConsent(overrides: Partial<ManagerTransferConsent> = {}): ManagerTransferConsent {
  return {
    id: 77,
    application_id: 12,
    space_id: 42,
    space_name: '王家族谱',
    space_kind: 'lineage',
    applicant_user_id: 5,
    applicant_name: '李小妹',
    current_manager_user_id: 1,
    status: 'pending',
    requested_at: '2026-08-31T00:00:00',
    responded_at: null,
    response_reason: null,
    ...overrides,
  }
}

const Host = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(SpaceManagerApplicationPanel))])
  },
})

async function mountPanel(): Promise<ReturnType<typeof mount>> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, { global: { plugins: [pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

// n-modal 内容 teleport 到 body，交互统一走 document 查询
function clickInBody(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

describe('SpaceManagerApplicationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('服务端没有候选目标时入口不出现（资格判定不在前端）', async () => {
    mockedEligibleTargets.mockResolvedValue([])
    const wrapper = await mountPanel()
    expect(wrapper.find('[data-test="apply-space-admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('申请接手：提交的是所选族谱空间，并展示目标名称与现任管理员', async () => {
    mockedEligibleTargets.mockResolvedValue([makeTarget()])
    mockedSubmit.mockResolvedValue(makeApplication({ id: 12, space_id: 42, space_name: '王家族谱' }))
    const wrapper = await mountPanel()

    await wrapper.find('[data-test="apply-space-admin"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="apply-target-42"]')?.textContent).toContain('王家族谱')
    expect(document.querySelector('[data-test="apply-target-42"]')?.textContent).toContain('王大伯')

    clickInBody('[data-test="apply-target-submit-42"]')
    await vi.waitFor(() => expect(mockedSubmit).toHaveBeenCalledWith('space_admin', { spaceId: 42 }))
    wrapper.unmount()
  })

  it('已有在办申请的目标：提交按钮禁用', async () => {
    mockedEligibleTargets.mockResolvedValue([makeTarget({ has_pending_application: true })])
    const wrapper = await mountPanel()

    await wrapper.find('[data-test="apply-space-admin"]').trigger('click')
    await flushPromises()
    const submit = document.querySelector<HTMLButtonElement>('[data-test="apply-target-submit-42"]')
    expect(submit?.disabled).toBe(true)
    expect(mockedSubmit).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('原管理员工单：同意后调用后端；谢绝缺理由时前端先拦下', async () => {
    mockedMyConsents.mockResolvedValue([makeConsent()])
    const wrapper = await mountPanel()

    const row = wrapper.find('[data-test="transfer-consent-row"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('李小妹')
    expect(row.text()).toContain('王家族谱')
    // 交接后果必须写在按钮旁边，不能只靠成功提示事后说明
    expect(row.text()).toContain('降为普通成员')

    await wrapper.find('[data-test="consent-reject-77"]').trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('谢绝交接需要填写理由')
    expect(mockedRespondConsent).not.toHaveBeenCalled()

    mockedRespondConsent.mockResolvedValue(makeConsent({ status: 'accepted' }))
    await wrapper.find('[data-test="consent-accept-77"]').trigger('click')
    await flushPromises()
    expect(mockedRespondConsent).toHaveBeenCalledWith(77, 'accept', undefined)
    wrapper.unmount()
    mockedMyConsents.mockResolvedValue([])
  })

  it('我的申请状态行：审批中/未通过徽章并展示平台备注', async () => {
    vi.mocked(spacesApi.fetchMyManagerApplications).mockResolvedValue([
      makeApplication({ id: 21 }),
      makeApplication({
        id: 22,
        space_id: 1,
        space_name: '我们家',
        status: 'rejected',
        decision_note: '请先完成更多成员邀请',
        decided_at: '2026-08-31T00:00:00',
      }),
    ])
    const wrapper = await mountPanel()

    const rows = wrapper.findAll('[data-test="manager-application-row"]')
    await vi.waitFor(() => expect(rows.length).toBe(2))
    expect(rows[0]!.text()).toContain('我们家')
    expect(rows[0]!.text()).toContain('审批中')
    expect(rows[1]!.text()).toContain('未通过')
    expect(rows[1]!.text()).toContain('平台备注：请先完成更多成员邀请')
    wrapper.unmount()
  })
})
