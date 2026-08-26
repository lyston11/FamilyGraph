import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import { ACTION_CARD_ERRORS } from '@/api/actionCards'
import * as actionCardsApi from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import ActionCardInbox from '@/components/actioncard/ActionCardInbox.vue'
import { useActionCardsStore } from '@/stores/actionCards'
import { useSpacesStore } from '@/stores/spaces'
import type { ActionCard } from '@/types/actionCard'

vi.mock('@/api/actionCards', () => ({
  ACTION_CARD_ERRORS: {
    CARD_STATE_CONFLICT: 'CARD_STATE_CONFLICT',
    CARD_EXPIRED: 'CARD_EXPIRED',
    CARD_EXECUTE_REJECTED: 'CARD_EXECUTE_REJECTED',
    SPACE_FORBIDDEN_ACTOR: 'SPACE_FORBIDDEN_ACTOR',
  },
  fetchActionCards: vi.fn(),
  viewActionCard: vi.fn(),
  dismissActionCard: vi.fn(),
  acceptActionCard: vi.fn(),
  executeActionCard: vi.fn(),
  friendlyActionCardError: (code: string) => code,
}))

const mockedFetch = vi.mocked(actionCardsApi.fetchActionCards)

function makeCard(overrides: Partial<ActionCard> = {}): ActionCard {
  return {
    id: 1,
    kind: 'lineage_request',
    space_id: 7,
    subject_user: { id: 10, name: '张三' },
    object_user: null,
    reason_text: '你们是堂兄弟',
    evidence: { fact_ids: [3], path_summary: null, evidence_version: 1 },
    proposed_action: { type: 'request_lineage', params: {} },
    privacy_effect: '对方将看到你的名字',
    state: 'pending',
    expires_at: null,
    created_at: '2026-08-26T00:00:00',
    revision: 1,
    ...overrides,
  }
}

let pinia: Pinia

async function mountInbox(): Promise<ReturnType<typeof mount>> {
  const wrapper = mount(ActionCardInbox, {
    global: { plugins: [pinia, ElementPlus] },
  })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('ActionCardInbox（V2.4 Block S3）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      { id: 7, name: '我家', owner_id: 1, kind: 'household', created_at: '', pending_count: 0, member_count: 1 },
      { id: 8, name: '族谱', owner_id: 1, kind: 'lineage', created_at: '', pending_count: 0, member_count: 2 },
    ]
    spaces.currentSpaceId = 7
  })

  it('加载后入口可见，badge 显示 pending 数；点击展开面板渲染卡片', async () => {
    mockedFetch.mockResolvedValue([
      makeCard(),
      makeCard({ id: 2, state: 'accepted' }),
      makeCard({ id: 3, state: 'dismissed' }),
    ])
    const wrapper = await mountInbox()

    expect(wrapper.find('[data-test="steward-inbox-entry"]').exists()).toBe(true)
    const badge = wrapper.find('[data-test="steward-inbox-badge"]')
    expect(badge.text()).toContain('1')

    await wrapper.find('[data-test="steward-inbox-entry"]').trigger('click')
    expect(wrapper.find('[data-test="steward-inbox-panel"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="action-card-item"]')).toHaveLength(3)
    expect(wrapper.find('[data-test="steward-inbox-empty"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('空列表：面板展示友好空态文案', async () => {
    mockedFetch.mockResolvedValue([])
    const wrapper = await mountInbox()

    await wrapper.find('[data-test="steward-inbox-entry"]').trigger('click')
    expect(wrapper.find('[data-test="steward-inbox-empty"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('管家暂时没有新的建议')
    wrapper.unmount()
  })

  it('403 SPACE_FORBIDDEN_ACTOR：入口降级隐藏', async () => {
    mockedFetch.mockRejectedValue(
      new ApiError(403, ACTION_CARD_ERRORS.SPACE_FORBIDDEN_ACTOR, '无权限'),
    )
    const wrapper = await mountInbox()

    expect(wrapper.find('[data-test="steward-inbox-entry"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('503（flag 关闭）：入口降级隐藏', async () => {
    mockedFetch.mockRejectedValue(new ApiError(503, 'STEWARD_FLAG_DISABLED', '未启用'))
    const wrapper = await mountInbox()

    expect(wrapper.find('[data-test="steward-inbox-entry"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('切换空间：重新加载新空间列表（旧空间由 resetForSpace 清理）', async () => {
    mockedFetch.mockResolvedValue([makeCard()])
    const wrapper = await mountInbox()
    expect(mockedFetch).toHaveBeenLastCalledWith(7)

    const spaces = useSpacesStore(pinia)
    spaces.currentSpaceId = 8
    await new Promise((resolve) => setTimeout(resolve))

    expect(mockedFetch).toHaveBeenLastCalledWith(8)
    // 面板随空间切换收起
    expect(wrapper.find('[data-test="steward-inbox-panel"]').exists()).toBe(false)

    const store = useActionCardsStore(pinia)
    store.resetForSpace(7)
    expect(store.cardsOf(7)).toHaveLength(0)
    wrapper.unmount()
  })
})
