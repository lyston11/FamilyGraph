import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { decodePersonalFamilyView, fetchPersonalFamilyView } from '@/api/personalFamilyView'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

function makePayload() {
  return {
    space_id: 7,
    status: 'current',
    view_version: 3,
    computed_at: null,
    nodes: [],
    edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
  }
}

describe('personal family view API', () => {
  it('requests only the selected space and decodes a valid snapshot', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makePayload(), headers: {} })

    const snapshot = await fetchPersonalFamilyView(7)
    expect(snapshot).toMatchObject({ data: { space_id: 7 }, etag: null })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/personal-family-view',
      expect.objectContaining({ params: { space_id: 7 } }),
    )
  })

  it('rejects malformed snapshots instead of passing them to the store', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { space_id: 7, nodes: [] } })

    await expect(fetchPersonalFamilyView(7)).rejects.toThrow('个人家族视图响应格式无效')
  })

  it('sends If-None-Match and retains 304 metadata without inventing a display deadline', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 304, data: undefined, headers: {} })

    await expect(fetchPersonalFamilyView(7, 'W/"v3"')).resolves.toMatchObject({ notModified: true, etag: null, displayExpiresAt: null })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/personal-family-view',
      expect.objectContaining({
        params: { space_id: 7 },
        headers: { 'If-None-Match': 'W/"v3"' },
      }),
    )
  })

  it('captures the response ETag into the snapshot for conditional revalidation', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: makePayload(),
      headers: { etag: 'W/"v4"' },
    })

    const snapshot = await fetchPersonalFamilyView(7)
    expect(snapshot?.etag).toBe('W/"v4"')
  })
})

describe('topology_edges 解码（09-13 family-tree-relationship-topology）', () => {
  const member = {
    user_id: 1,
    display: {
      id: 1,
      name: '本人',
      gender: 'm',
      birth: null,
      death: null,
      bio: null,
      avatar_path: null,
      privacy_mode: 'perpetual',
      claim_status: 'claimed',
    },
    visibility_level: 'household_detail',
    inclusion_reason_code: 'root',
  }
  const spouse = {
    ...member,
    user_id: 2,
    display: { ...member.display, id: 2, name: '配偶' },
    inclusion_reason_code: 'confirmed_path',
  }

  it('字段缺失（旧后端载荷）→ topology_edges=null，区别于合法空数组', () => {
    const legacy = decodePersonalFamilyView({ ...makePayload(), nodes: [member] })
    expect(legacy.topology_edges).toBeNull()

    const empty = decodePersonalFamilyView({
      ...makePayload(),
      nodes: [member],
      topology_edges: [],
    })
    expect(empty.topology_edges).toEqual([])
  })

  it('字段存在但不是数组 → 拒绝整个载荷', () => {
    expect(() =>
      decodePersonalFamilyView({ ...makePayload(), nodes: [member], topology_edges: 'oops' }),
    ).toThrow('个人家族视图响应格式无效')
  })

  it('合法结构边解码；端点必须命中已解码节点集合，否则丢弃', () => {
    const payload = {
      ...makePayload(),
      nodes: [member, spouse],
      topology_edges: [
        { id: 'spouse:-:1:2', from_user_id: 1, to_user_id: 2, edge_kind: 'spouse', subtype: null },
        // 悬空端点（对端不在节点集合内）：丢弃
        { id: 'parent:biological:9:1', from_user_id: 9, to_user_id: 1, edge_kind: 'parent', subtype: 'biological' },
      ],
    }
    const decoded = decodePersonalFamilyView(payload)
    expect(decoded.topology_edges).toEqual([
      { id: 'spouse:-:1:2', from_user_id: 1, to_user_id: 2, edge_kind: 'spouse', subtype: null },
    ])
  })

  it('坏条目按条丢弃：非正整数端点、自环、非法 kind、parent 缺子类型、对称携带子类型', () => {
    const payload = {
      ...makePayload(),
      nodes: [member, spouse],
      topology_edges: [
        { id: 'a', from_user_id: 0, to_user_id: 2, edge_kind: 'spouse', subtype: null },
        { id: 'b', from_user_id: 1, to_user_id: 1, edge_kind: 'spouse', subtype: null },
        { id: 'c', from_user_id: 1, to_user_id: 2, edge_kind: 'cousin', subtype: null },
        { id: 'd', from_user_id: 1, to_user_id: 2, edge_kind: 'parent', subtype: null },
        { id: 'e', from_user_id: 1, to_user_id: 2, edge_kind: 'spouse', subtype: 'biological' },
        { id: 'f', from_user_id: 1.5, to_user_id: 2, edge_kind: 'spouse', subtype: null },
        { id: 'g', from_user_id: 2, to_user_id: 1, edge_kind: 'sibling', subtype: null },
        'garbage',
      ],
    }
    const decoded = decodePersonalFamilyView(payload)
    expect(decoded.topology_edges).toEqual([
      { id: 'g', from_user_id: 2, to_user_id: 1, edge_kind: 'sibling', subtype: null },
    ])
  })

  it('重复 id 防御去重', () => {
    const payload = {
      ...makePayload(),
      nodes: [member, spouse],
      topology_edges: [
        { id: 'spouse:-:1:2', from_user_id: 1, to_user_id: 2, edge_kind: 'spouse', subtype: null },
        { id: 'spouse:-:1:2', from_user_id: 2, to_user_id: 1, edge_kind: 'spouse', subtype: null },
      ],
    }
    const decoded = decodePersonalFamilyView(payload)
    expect(decoded.topology_edges).toHaveLength(1)
  })
})
