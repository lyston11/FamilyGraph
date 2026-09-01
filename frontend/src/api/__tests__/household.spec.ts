import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { decodeHouseholdCard, fetchHouseholdCard } from '@/api/household'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

function makeDisplayFixture(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    name: `成员${id}`,
    gender: 'f',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'perpetual',
    claim_status: 'claimed',
    ...overrides,
  }
}

function makeCardFixture(overrides: Record<string, unknown> = {}) {
  return {
    space_id: 7,
    space_kind: 'household',
    space_name: '我的家庭',
    view_version: 3,
    computed_at: '2026-09-01T08:00:00',
    viewer: makeDisplayFixture(10),
    members: [
      {
        user_id: 10,
        display: makeDisplayFixture(10),
        household_label: '管理员',
        visibility_level: 'household_detail',
      },
      {
        user_id: 20,
        display: makeDisplayFixture(20, { gender: { __masked__: true } }),
        household_label: '成员',
        visibility_level: 'household_detail',
      },
    ],
    allowed_actions: { can_invite_members: true, can_create_household: false, empty_state_hint: null },
    ...overrides,
  }
}

describe('household card API（design.md §4.2 合同占位）', () => {
  it('按明确 space_id 请求约定路径并解码合法投影', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makeCardFixture(), headers: {} })

    const snapshot = await fetchHouseholdCard(7)
    expect(snapshot).toMatchObject({
      data: { space_id: 7, space_kind: 'household', space_name: '我的家庭' },
      etag: null,
    })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/household-card',
      expect.objectContaining({ params: { space_id: 7 } }),
    )
  })

  it('保留字段级 masked 哨兵，不把遮罩解释为明文', () => {
    const card = decodeHouseholdCard(makeCardFixture())
    expect(card.members[1]?.display.gender).toEqual({ __masked__: true })
    expect(card.members[0]?.display.gender).toBe('f')
  })

  it('解码空成员与空状态提示（允许动作由服务端给出）', () => {
    const card = decodeHouseholdCard(
      makeCardFixture({
        members: [],
        allowed_actions: {
          can_invite_members: false,
          can_create_household: true,
          empty_state_hint: '还没有家庭成员，可邀请家人加入',
        },
      }),
    )
    expect(card.members).toEqual([])
    expect(card.allowed_actions).toEqual({
      can_invite_members: false,
      can_create_household: true,
      empty_state_hint: '还没有家庭成员，可邀请家人加入',
    })
  })

  it.each([
    ['成员可见性层级未知', { user_id: 20, display: makeDisplayFixture(20), household_label: '成员', visibility_level: 'invisible' }],
    ['成员缺 household_label', { user_id: 20, display: makeDisplayFixture(20), visibility_level: 'household_detail' }],
    ['成员 display 缺 baseline 字段', { user_id: 20, display: { id: 20 }, household_label: '成员', visibility_level: 'household_detail' }],
    ['成员不是对象', 20],
  ])('坏成员丢弃（%s），其余成员保留', (_reason, badMember) => {
    const goodMember = {
      user_id: 10,
      display: makeDisplayFixture(10),
      household_label: '管理员',
      visibility_level: 'household_detail',
    }
    const card = decodeHouseholdCard(makeCardFixture({ members: [goodMember, badMember] }))
    expect(card.members.map((member) => member.user_id)).toEqual([10])
  })

  it.each([
    ['space_kind 不是 household', { space_kind: 'lineage' }],
    ['缺 space_name', { space_name: undefined }],
    ['view_version 缺失', { view_version: undefined }],
    ['viewer display 非法', { viewer: { id: 10 } }],
    ['allowed_actions 非法', { allowed_actions: { can_invite_members: 'yes' } }],
    ['members 不是数组', { members: undefined }],
  ])('顶层合同破坏时整份拒绝（%s）', (_reason, overrides) => {
    expect(() => decodeHouseholdCard(makeCardFixture(overrides))).toThrow('家庭卡响应格式无效')
  })

  it('304 返回 null，由 store 保留既有快照；ETag 写入快照', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 304, data: undefined, headers: {} })
    await expect(fetchHouseholdCard(7, 'W/"v3"')).resolves.toBeNull()
    expect(apiClient.get).toHaveBeenCalledWith(
      '/household-card',
      expect.objectContaining({
        params: { space_id: 7 },
        headers: { 'If-None-Match': 'W/"v3"' },
      }),
    )

    vi.mocked(apiClient.get).mockResolvedValue({
      data: makeCardFixture(),
      headers: { etag: 'W/"v4"' },
    })
    await expect(fetchHouseholdCard(7)).resolves.toMatchObject({ etag: 'W/"v4"' })
  })
})
