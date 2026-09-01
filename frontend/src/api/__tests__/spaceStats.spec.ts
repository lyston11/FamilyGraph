import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { decodeSpaceStats, fetchSpaceStats } from '@/api/spaceStats'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

function makeStatsFixture(overrides: Record<string, unknown> = {}) {
  return {
    space_id: 7,
    space_kind: 'lineage',
    status: 'current',
    view_version: 5,
    node_count: 12,
    edge_count: 9,
    member_count: 4,
    relation_distribution: [
      { dir_class: 'elder', count: 4 },
      { dir_class: 'spouse', count: 2 },
    ],
    pending_action_cards: 1,
    pending_memberships: 2,
    computed_at: '2026-09-01T08:00:00',
    stale_reason: null,
    ...overrides,
  }
}

describe('spaceStats API（design.md §4.4 合同占位）', () => {
  it('请求 /stats?space_id=<id> 并解码合法聚合', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makeStatsFixture(), headers: {} })

    const snapshot = await fetchSpaceStats(7)
    expect(snapshot).toMatchObject({
      data: { space_id: 7, node_count: 12, member_count: 4 },
      etag: null,
    })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/stats',
      expect.objectContaining({ params: { space_id: 7 } }),
    )
  })

  it('未知 dir_class 切片丢弃，合法切片保留', () => {
    const stats = decodeSpaceStats(
      makeStatsFixture({
        relation_distribution: [
          { dir_class: 'elder', count: 4 },
          { dir_class: 'ancestor', count: 99 },
          { count: 3 },
          'bad',
        ],
      }),
    )
    expect(stats.relation_distribution).toEqual([{ dir_class: 'elder', count: 4 }])
  })

  it('household 空间与空 distribution 同样可解码；view_version 可为 null', () => {
    const stats = decodeSpaceStats(
      makeStatsFixture({
        space_kind: 'household',
        view_version: null,
        relation_distribution: [],
        stale_reason: 'membership_revoked',
      }),
    )
    expect(stats.space_kind).toBe('household')
    expect(stats.view_version).toBeNull()
    expect(stats.stale_reason).toBe('membership_revoked')
  })

  it.each([
    ['未知 status', { status: 'computing' }],
    ['未知 space_kind', { space_kind: 'clan' }],
    ['缺 node_count', { node_count: undefined }],
    ['distribution 不是数组', { relation_distribution: {} }],
    ['缺 pending_memberships', { pending_memberships: undefined }],
  ])('顶层合同破坏时整份拒绝（%s）', (_reason, overrides) => {
    expect(() => decodeSpaceStats(makeStatsFixture(overrides))).toThrow('空间统计响应格式无效')
  })

  it('304 返回 null 并发送 If-None-Match；ETag 写入快照', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 304, data: undefined, headers: {} })
    await expect(fetchSpaceStats(7, 'W/"s5"')).resolves.toBeNull()
    expect(apiClient.get).toHaveBeenCalledWith(
      '/stats',
      expect.objectContaining({
        params: { space_id: 7 },
        headers: { 'If-None-Match': 'W/"s5"' },
      }),
    )

    vi.mocked(apiClient.get).mockResolvedValue({
      data: makeStatsFixture(),
      headers: { etag: 'W/"s6"' },
    })
    await expect(fetchSpaceStats(7)).resolves.toMatchObject({ etag: 'W/"s6"' })
  })
})
