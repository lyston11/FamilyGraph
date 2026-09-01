import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

import { ApiError } from '@/api/errors'
import * as spaceStatsApi from '@/api/spaceStats'
import StatsView from '@/views/StatsView.vue'
import { useSpacesStore } from '@/stores/spaces'
import type { SpaceStatsSnapshot } from '@/types/api'

vi.mock('@/api/spaceStats', () => ({
  fetchSpaceStats: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
}))

const mockedFetchSpaceStats = vi.mocked(spaceStatsApi.fetchSpaceStats)

function makeStats(overrides: Record<string, unknown> = {}): SpaceStatsSnapshot {
  return {
    data: {
      space_id: 7,
      space_kind: 'household',
      status: 'current',
      view_version: 5,
      node_count: 12,
      edge_count: 9,
      member_count: 4,
      relation_distribution: [
        { dir_class: 'elder', count: 3 },
        { dir_class: 'younger', count: 4 },
        { dir_class: 'spouse', count: 2 },
      ],
      pending_action_cards: 2,
      pending_memberships: 1,
      computed_at: '2026-09-01T08:00:00',
      stale_reason: null,
      ...overrides,
    },
    etag: 'W/"s1"',
  }
}

let pinia: Pinia

async function mountStats(): Promise<VueWrapper> {
  const Harness = defineComponent({
    render() {
      return h('div', [h(StatsView)])
    },
  })
  const wrapper = mount(Harness, { global: { plugins: [pinia] } })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('StatsView（PRD §2.6：只按当前空间的服务端授权聚合）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pinia = createPinia()
    const spaces = useSpacesStore(pinia)
    spaces.spaces = [
      { id: 7, name: '我家', owner_id: 1, kind: 'household', created_at: '', pending_count: 0, member_count: 4 },
      { id: 12, name: '张氏家族', owner_id: 2, kind: 'lineage', created_at: '', pending_count: 0, member_count: 9 },
    ]
    spaces.currentSpaceId = 7
    mockedFetchSpaceStats.mockResolvedValue(makeStats())
  })

  it('只按当前 space_id 请求；渲染授权聚合摘要卡（不做跨空间总计）', async () => {
    const wrapper = await mountStats()

    // 只有一个请求，且只带当前空间
    expect(mockedFetchSpaceStats).toHaveBeenCalledTimes(1)
    expect(mockedFetchSpaceStats).toHaveBeenCalledWith(7, null)

    expect(wrapper.find('[data-test="stat-node-count"]').text()).toBe('12')
    expect(wrapper.find('[data-test="stat-edge-count"]').text()).toBe('9')
    expect(wrapper.find('[data-test="stat-member-count"]').text()).toBe('4')
    expect(wrapper.find('[data-test="stat-pending-cards"]').text()).toBe('2')
    expect(wrapper.find('[data-test="stat-pending-memberships"]').text()).toBe('1')

    // 关系分布按服务端 dir_class 切片渲染
    const distribution = wrapper.find('[data-test="stats-distribution"]').text()
    expect(distribution).toContain('长辈')
    expect(distribution).toContain('晚辈')
    expect(distribution).toContain('配偶')

    // 更新时间 / 版本来自服务端载荷
    expect(wrapper.find('[data-test="stats-computed-at"]').text()).toContain('2026-09-01 08:00')

    // 无跨空间总计：household 聚合不与 lineage 空间数字相加；无旧页「总人数」口径
    expect(wrapper.text()).not.toContain('总人数')
    expect(wrapper.text()).not.toContain('21') // 12 + 9（两个空间 node 数之和）不得出现
    wrapper.unmount()
  })

  it('6 态：never_computed/queued/running/failed 展示状态面板而非数字', async () => {
    mockedFetchSpaceStats.mockResolvedValue(
      makeStats({ status: 'running', node_count: 0, edge_count: 0, member_count: 0 }),
    )
    const wrapper = await mountStats()

    const panel = wrapper.find('[data-test="stats-status-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('统计计算中')
    expect(wrapper.find('[data-test="stat-node-count"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('6 态：stale 显示数据 + 明确过期标注；failed 无数据只显示失败面板', async () => {
    mockedFetchSpaceStats.mockResolvedValue(
      makeStats({ status: 'stale', stale_reason: 'relation_changed' }),
    )
    const wrapper = await mountStats()
    expect(wrapper.find('[data-test="stats-stale-alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="stat-node-count"]').text()).toBe('12')
    wrapper.unmount()

    mockedFetchSpaceStats.mockResolvedValue(makeStats({ status: 'failed', stale_reason: 'compute_error' }))
    const failed = await mountStats()
    const panel = failed.find('[data-test="stats-status-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('统计计算失败')
    expect(failed.find('[data-test="stat-node-count"]').exists()).toBe(false)
    failed.unmount()
  })

  it('刷新按钮强制重读服务端（跳过 ETag），仍然只针对当前空间', async () => {
    const wrapper = await mountStats()
    mockedFetchSpaceStats.mockClear()
    mockedFetchSpaceStats.mockResolvedValue(makeStats())

    await wrapper.find('[data-test="stats-refresh"]').trigger('click')
    await vi.waitFor(() => expect(mockedFetchSpaceStats).toHaveBeenCalledWith(7, null))
    expect(mockedFetchSpaceStats).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('404（BLOCKER 合同占位）→「统计服务合同未就绪」安全态，不回退旧 /stats', async () => {
    mockedFetchSpaceStats.mockRejectedValue(new ApiError(404, 'HTTP_ERROR', 'not found'))
    const wrapper = await mountStats()

    expect(wrapper.find('[data-test="stats-contract-unready"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('12')
    expect(wrapper.find('[data-test="stat-node-count"]').exists()).toBe(false)
    // 旧无空间合同不回退：只有带 space_id 的一次请求
    expect(mockedFetchSpaceStats).toHaveBeenCalledTimes(1)
    expect(mockedFetchSpaceStats).toHaveBeenCalledWith(7, null)
    wrapper.unmount()
  })

  it('lineage 空间：显示当前家族视图聚合口径', async () => {
    const spaces = useSpacesStore(pinia)
    spaces.currentSpaceId = 12
    mockedFetchSpaceStats.mockResolvedValue(makeStats({ space_kind: 'lineage' }))
    const wrapper = await mountStats()

    expect(mockedFetchSpaceStats).toHaveBeenCalledWith(12, null)
    expect(wrapper.find('[data-test="stats-view"]').text()).toContain('当前家族视图聚合')
    wrapper.unmount()
  })
})
