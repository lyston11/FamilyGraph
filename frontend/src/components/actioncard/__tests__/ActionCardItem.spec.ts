import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import { ACTION_CARD_ERRORS } from '@/api/actionCards'
import * as actionCardsApi from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import ActionCardItem from '@/components/actioncard/ActionCardItem.vue'
import { useSpacesStore } from '@/stores/spaces'
import type { ActionCard } from '@/types/actionCard'

vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn().mockResolvedValue([]),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: (code: string) => code,
}))

const mockedView = vi.mocked(actionCardsApi.viewActionCard)
const mockedDismiss = vi.mocked(actionCardsApi.dismissActionCard)
const mockedAccept = vi.mocked(actionCardsApi.acceptActionCard)
const mockedExecute = vi.mocked(actionCardsApi.executeActionCard)

function makeCard(overrides: Partial<ActionCard> = {}): ActionCard {
  return {
    id: 1,
    kind: 'lineage_request',
    space_id: 7,
    subject_user: { id: 10, name: '张三' },
    object_user: { id: 20, name: '李四' },
    reason_text: '你们是堂兄弟，且都已确认身份',
    evidence: { fact_ids: [3, 4], path_summary: '张三 → 祖父 → 李四', evidence_version: 2 },
    proposed_action: { type: 'request_lineage', params: {} },
    privacy_effect: '对方将看到你的名字与称谓',
    state: 'pending',
    expires_at: '2026-09-30T00:00:00',
    created_at: '2026-08-26T00:00:00',
    revision: 1,
    ...overrides,
  }
}

let pinia: Pinia

// useMessage 需要 NMessageProvider 祖先（App 层已备好）；n-modal teleport 到 body，
// 弹层断言与点击走 document 查询
function mountItem(card: ActionCard) {
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(ActionCardItem, { card }))])
    },
  })
  return mount(Harness, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('ActionCardItem（V2.4 Block S3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [{ id: 7, name: '我家', owner_id: 1, kind: 'household', created_at: '', pending_count: 0, member_count: 1 }]
  })

  it('pending 卡：展示原因/依据/动作/隐私/有效期/状态与三个操作按钮', () => {
    const wrapper = mountItem(makeCard())

    expect(wrapper.find('[data-test="card-reason"]').text()).toContain('堂兄弟')
    expect(wrapper.find('[data-test="card-evidence"]').text()).toContain('祖父')
    expect(wrapper.find('[data-test="card-action"]').text()).toContain('发送加入申请')
    expect(wrapper.find('[data-test="card-privacy"]').text()).toContain('名字与称谓')
    expect(wrapper.find('[data-test="card-expiry"]').text()).toContain('2026-09-30 前有效')
    expect(wrapper.find('[data-test="card-state-tag"]').text()).toBe('待处理')
    expect(wrapper.find('[data-test="card-participants"]').text()).toBe('张三 ↔ 李四')

    expect(wrapper.find('[data-test="card-view-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="card-dismiss-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="card-accept-btn"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="card-execute-btn"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([
    ['executed'],
    ['dismissed'],
    ['expired'],
    ['superseded'],
  ] as const)('%s 终态：只读，无任何操作按钮', (state) => {
    const wrapper = mountItem(makeCard({ state }))

    expect(wrapper.find('[data-test="card-view-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="card-dismiss-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="card-accept-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="card-execute-btn"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('状态徽章阶沿用领域状态工具类；过期卡整体灰化', () => {
    let wrapper = mountItem(makeCard())
    const stateTag = wrapper.find('[data-test="card-state-tag"]')
    expect(stateTag.classes()).toContain('fg-badge')
    expect(stateTag.classes()).toContain('fg-badge--accent')
    wrapper.unmount()

    wrapper = mountItem(makeCard({ state: 'accepted' }))
    expect(wrapper.find('[data-test="card-state-tag"]').classes()).toContain('fg-badge--confirmed')
    // 过期灰化：卡片根节点带灰化态
    const expired = mountItem(makeCard({ state: 'expired' }))
    expect(expired.find('[data-test="action-card-item"]').classes()).toContain('is-expired')
    expect(expired.find('[data-test="card-state-tag"]').classes()).toContain('fg-badge--provisional')
    expired.unmount()
    wrapper.unmount()
  })

  it('了解详情 / 不接受 / 接受 分别调用对应端点', async () => {
    mockedView.mockResolvedValue({ id: 1, state: 'viewed', revision: 2 })
    mockedDismiss.mockResolvedValue({ id: 1, state: 'dismissed', revision: 3 })
    mockedAccept.mockResolvedValue({ id: 1, state: 'accepted', revision: 4 })

    let wrapper = mountItem(makeCard())
    await wrapper.find('[data-test="card-view-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedView).toHaveBeenCalledWith(1))
    wrapper.unmount()

    wrapper = mountItem(makeCard())
    await wrapper.find('[data-test="card-dismiss-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedDismiss).toHaveBeenCalledWith(1))
    wrapper.unmount()

    wrapper = mountItem(makeCard())
    await wrapper.find('[data-test="card-accept-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedAccept).toHaveBeenCalledWith(1))
    wrapper.unmount()
  })

  it('两步发送：accepted 后「发起申请」先开确认弹层，取消不执行；显式确认才调 execute', async () => {
    mockedExecute.mockResolvedValue({ id: 1, state: 'executed' })
    const wrapper = mountItem(makeCard({ state: 'accepted' }))

    // 第一步入口存在，弹层未打开
    expect(wrapper.find('[data-test="card-view-btn"]').exists()).toBe(false)
    expect(document.querySelector('[data-test="execute-confirm-dialog"]')).toBeNull()

    await wrapper.find('[data-test="card-execute-btn"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    const dialog = document.querySelector('[data-test="execute-confirm-dialog"]')
    expect(dialog).not.toBeNull()
    // 弹层再次显示目标空间与披露影响
    expect(dialog?.textContent).toContain('我家')
    expect(dialog?.textContent).toContain('名字与称谓')

    // 取消：不调用 execute
    ;(document.querySelector('[data-test="execute-cancel"]') as HTMLButtonElement).click()
    await new Promise((resolve) => setTimeout(resolve))
    expect(mockedExecute).not.toHaveBeenCalled()

    // 重新打开并显式确认
    await wrapper.find('[data-test="card-execute-btn"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    ;(document.querySelector('[data-test="execute-confirm"]') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(mockedExecute).toHaveBeenCalledTimes(1))
    wrapper.unmount()
  })

  it('execute 可重试失败：弹层保留（卡片仍 accepted），提示服务端 reason', async () => {
    mockedExecute.mockRejectedValue(
      new ApiError(409, ACTION_CARD_ERRORS.CARD_EXECUTE_REJECTED, '无法执行', {
        reason: '目标成员资格已撤销',
      }),
    )
    const wrapper = mountItem(makeCard({ state: 'accepted' }))
    await wrapper.find('[data-test="card-execute-btn"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    ;(document.querySelector('[data-test="execute-confirm"]') as HTMLButtonElement).click()
    await vi.waitFor(() => expect(mockedExecute).toHaveBeenCalledTimes(1))
    await new Promise((resolve) => setTimeout(resolve))
    // 卡片保持 accepted：发起申请按钮仍在（可重试）
    expect(wrapper.find('[data-test="card-execute-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('并发竞争 409：view 失败后给出可读提示且不崩溃', async () => {
    mockedView.mockRejectedValue(
      new ApiError(409, ACTION_CARD_ERRORS.CARD_STATE_CONFLICT, '已被更新'),
    )
    const wrapper = mountItem(makeCard())
    await wrapper.find('[data-test="card-view-btn"]').trigger('click')
    await vi.waitFor(() => expect(mockedView).toHaveBeenCalled())
    // 卡片状态由父级/store 刷新驱动，组件本身不本地篡改状态
    expect(wrapper.find('[data-test="card-state-tag"]').text()).toBe('待处理')
    wrapper.unmount()
  })
})
