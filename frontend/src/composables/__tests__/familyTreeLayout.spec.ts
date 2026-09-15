import { describe, expect, it } from 'vitest'

import {
  computeFamilyTreeLayout,
  LAYOUT_CARD_WIDTH,
  LAYOUT_COL_SPACING,
  LAYOUT_ROW_SPACING,
} from '@/composables/familyTreeLayout'
import type { PersonalFamilyViewTopologyEdge } from '@/types/api'

/**
 * 家族树确定性世代布局纯函数（09-13 design.md §6）：
 * - 世代约束：parent 子女=家长+1，对称边同代；共享父母 → 兄妹同代；
 * - 约束矛盾返回 null（视图回退自由画布），绝不静默删除关系；
 * - 夫妻/伴侣横向块相邻、同代卡片不重叠、孤立节点不丢失、输入乱序结果不变。
 */

function edge(
  kind: PersonalFamilyViewTopologyEdge['edge_kind'],
  from: number,
  to: number,
  subtype: PersonalFamilyViewTopologyEdge['subtype'] = null,
): PersonalFamilyViewTopologyEdge {
  return { id: `${kind}:${subtype ?? '-'}:${from}:${to}`, from_user_id: from, to_user_id: to, edge_kind: kind, subtype }
}

describe('computeFamilyTreeLayout：世代约束', () => {
  it('共同父母 + 本人 + 妹妹：父母上一代，兄妹同代', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3, 4],
      edges: [
        edge('parent', 2, 1, 'biological'),
        edge('parent', 2, 3, 'biological'),
        edge('parent', 4, 1, 'biological'),
        edge('parent', 4, 3, 'biological'),
      ],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    const byUser = positions!
    expect(byUser.get(1)!.y).toBe(0)
    expect(byUser.get(3)!.y).toBe(0)
    expect(byUser.get(2)!.y).toBe(-LAYOUT_ROW_SPACING)
    expect(byUser.get(4)!.y).toBe(-LAYOUT_ROW_SPACING)
  })

  it('多父母不合并：子女同一行，父母都在上一代', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3, 4],
      edges: [
        edge('parent', 2, 1, 'biological'),
        edge('parent', 3, 1, 'adoptive'),
        edge('parent', 4, 1, 'step'),
      ],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    expect(positions!.get(1)!.y).toBe(0)
    expect(positions!.get(2)!.y).toBe(-LAYOUT_ROW_SPACING)
    expect(positions!.get(3)!.y).toBe(-LAYOUT_ROW_SPACING)
    expect(positions!.get(4)!.y).toBe(-LAYOUT_ROW_SPACING)
  })

  it('孙辈只按实际父母逐代排列（不跨代直连）', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3, 4, 5],
      edges: [
        edge('spouse', 1, 2),
        edge('parent', 1, 3, 'biological'),
        edge('parent', 2, 3, 'biological'),
        edge('spouse', 3, 4),
        edge('parent', 3, 5, 'biological'),
        edge('parent', 4, 5, 'biological'),
      ],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    expect(positions!.get(3)!.y).toBe(LAYOUT_ROW_SPACING)
    expect(positions!.get(4)!.y).toBe(LAYOUT_ROW_SPACING)
    expect(positions!.get(5)!.y).toBe(2 * LAYOUT_ROW_SPACING)
  })

  it('父母未知的 direct_sibling：两端同代，不补造父母', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2],
      edges: [edge('sibling', 1, 2)],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    expect(positions!.get(1)!.y).toBe(0)
    expect(positions!.get(2)!.y).toBe(0)
  })
})

describe('computeFamilyTreeLayout：冲突回退', () => {
  it('parent 成环 → null', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2],
      edges: [edge('parent', 1, 2, 'biological'), edge('parent', 2, 1, 'biological')],
      viewerId: 1,
    })
    expect(positions).toBeNull()
  })

  it('对称同代与 parent 差一代矛盾 → null', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3],
      edges: [
        edge('parent', 1, 2, 'biological'),
        edge('spouse', 2, 3),
        edge('parent', 2, 3, 'biological'),
      ],
      viewerId: 1,
    })
    expect(positions).toBeNull()
  })
})

describe('computeFamilyTreeLayout：横向布局块与重叠', () => {
  it('配偶同块相邻（列距一个 COL_SPACING）', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3],
      edges: [edge('parent', 2, 1, 'biological'), edge('spouse', 1, 3)],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    const dx = Math.abs(positions!.get(1)!.x - positions!.get(3)!.x)
    expect(dx).toBe(LAYOUT_COL_SPACING)
    expect(positions!.get(1)!.y).toBe(positions!.get(3)!.y)
  })

  it('同代所有卡片两两不重叠（水平间距 ≥ 卡宽）', () => {
    // 两位家长各自带配偶、三名同代子女：同一行人数多，检验间距
    const userIds = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    const positions = computeFamilyTreeLayout({
      userIds,
      edges: [
        edge('parent', 2, 1, 'biological'),
        edge('parent', 3, 1, 'biological'),
        edge('spouse', 2, 4),
        edge('spouse', 3, 5),
        edge('parent', 2, 6, 'biological'),
        edge('parent', 3, 6, 'biological'),
        edge('parent', 2, 7, 'biological'),
        edge('parent', 3, 7, 'biological'),
        edge('parent', 2, 8, 'biological'),
        edge('parent', 3, 8, 'biological'),
      ],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    const byY = new Map<number, number[]>()
    for (const id of userIds) {
      const position = positions!.get(id)!
      const list = byY.get(position.y) ?? []
      list.push(position.x)
      byY.set(position.y, list)
    }
    for (const xs of byY.values()) {
      const sorted = [...xs].sort((a, b) => a - b)
      for (let i = 1; i < sorted.length; i += 1) {
        expect(sorted[i]! - sorted[i - 1]!).toBeGreaterThanOrEqual(LAYOUT_CARD_WIDTH)
      }
    }
  })

  it('再婚多配偶全部相邻保留（同块横排）', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 3, 4],
      edges: [edge('spouse', 1, 2), edge('spouse', 1, 3), edge('spouse', 1, 4)],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    const xs = [1, 2, 3, 4].map((id) => positions!.get(id)!.x).sort((a, b) => a - b)
    expect(xs[1]! - xs[0]!).toBe(LAYOUT_COL_SPACING)
    expect(xs[2]! - xs[1]!).toBe(LAYOUT_COL_SPACING)
    expect(xs[3]! - xs[2]!).toBe(LAYOUT_COL_SPACING)
  })
})

describe('computeFamilyTreeLayout：分支空间', () => {
  it('三代两分支：父母居中、后代区间分离，兄弟姐妹边不拆散夫妻', () => {
    const userIds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    const edges = [edge('spouse', 1, 2), edge('spouse', 3, 6), edge('spouse', 4, 5),
      edge('sibling', 3, 4),
      ...[1, 2].flatMap((parent) => [3, 4].map((child) => edge('parent', parent, child))),
      ...[3, 6].flatMap((parent) => [7, 8, 9].map((child) => edge('parent', parent, child))),
      ...[4, 5].flatMap((parent) => [10, 11].map((child) => edge('parent', parent, child))),
    ]
    const result = computeFamilyTreeLayout({ userIds, edges, viewerId: 1 })!
    const x = (id: number) => result.get(id)!.x
    expect(result.size).toBe(userIds.length)
    expect(x(6) - x(3)).toBe(LAYOUT_COL_SPACING)
    expect(x(5) - x(4)).toBe(LAYOUT_COL_SPACING)
    expect((x(3) + x(6)) / 2).toBe((x(7) + x(9)) / 2)
    expect((x(4) + x(5)) / 2).toBe((x(10) + x(11)) / 2)
    expect((x(1) + x(2)) / 2).toBe((x(7) + x(11)) / 2)
    expect(Math.max(...[3, 6, 7, 8, 9].map(x)) + LAYOUT_CARD_WIDTH)
      .toBeLessThan(Math.min(...[4, 5, 10, 11].map(x)))
    expect(result.get(11)!.y).toBe(2 * LAYOUT_ROW_SPACING)
    expect(computeFamilyTreeLayout({ userIds: [...userIds].reverse(), edges: [...edges].reverse(), viewerId: 1 }))
      .toEqual(result)
  })

  it('无配偶事实的共同父母居中于共享子女，并且不重复摆放后代', () => {
    const result = computeFamilyTreeLayout({ userIds: [1, 2, 3, 4, 5], viewerId: 1,
      edges: [edge('parent', 1, 3), edge('parent', 2, 3), edge('parent', 1, 4),
        edge('parent', 2, 4), edge('parent', 3, 5)],
    })!
    expect(result.size).toBe(5)
    expect((result.get(1)!.x + result.get(2)!.x) / 2)
      .toBe((result.get(3)!.x + result.get(4)!.x) / 2)
    expect(result.get(5)!.x).toBe(result.get(3)!.x)
  })

  it('跨支系婚姻保留唯一人物、所有世代和稳定无重叠坐标', () => {
    const userIds = [1, 2, 3, 4, 5, 6, 7, 8]
    const edges = [edge('parent', 1, 3), edge('parent', 2, 4), edge('spouse', 3, 4),
      edge('parent', 3, 5), edge('parent', 4, 5), edge('parent', 1, 6),
      edge('parent', 2, 7), edge('parent', 6, 8)]
    const result = computeFamilyTreeLayout({ userIds, edges, viewerId: 1 })!
    expect(result.size).toBe(userIds.length)
    for (const a of result.values()) {
      expect(Number.isFinite(a.x)).toBe(true)
      for (const b of result.values()) {
        if (a !== b && a.y === b.y) expect(Math.abs(a.x - b.x)).toBeGreaterThanOrEqual(LAYOUT_CARD_WIDTH)
      }
    }
    for (const link of edges.filter((link) => link.edge_kind === 'parent')) {
      expect(result.get(link.to_user_id)!.y - result.get(link.from_user_id)!.y).toBe(LAYOUT_ROW_SPACING)
    }
    expect(computeFamilyTreeLayout({ userIds: [...userIds].reverse(), edges: [...edges].reverse(), viewerId: 1 })).toEqual(result)
  })
})

describe('computeFamilyTreeLayout：分量、孤立节点与确定性', () => {
  it('孤立节点获得位置且不与已连接成员重叠', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 9],
      edges: [edge('parent', 2, 1, 'biological')],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    expect(positions!.has(9)).toBe(true)
    const isolated = positions!.get(9)!
    const viewer = positions!.get(1)!
    expect(isolated.x !== viewer.x || isolated.y !== viewer.y).toBe(true)
  })

  it('断开分量独立成带：不与本人分量重叠，也不编造亲缘', () => {
    const positions = computeFamilyTreeLayout({
      userIds: [1, 2, 5, 6],
      edges: [edge('parent', 2, 1, 'biological'), edge('parent', 5, 6, 'biological')],
      viewerId: 1,
    })
    expect(positions).not.toBeNull()
    // 分量间至少相隔一个行距
    const viewerY = positions!.get(1)!.y
    const otherY = positions!.get(5)!.y
    expect(Math.abs(otherY - viewerY)).toBeGreaterThanOrEqual(LAYOUT_ROW_SPACING)
  })

  it('输入顺序打乱结果逐点一致（确定性）', () => {
    const edges = [
      edge('parent', 2, 1, 'biological'),
      edge('parent', 2, 3, 'biological'),
      edge('parent', 4, 3, 'biological'),
      edge('spouse', 3, 5),
    ]
    const forward = computeFamilyTreeLayout({
      userIds: [1, 2, 3, 4, 5],
      edges,
      viewerId: 1,
    })
    const shuffled = computeFamilyTreeLayout({
      userIds: [5, 3, 1, 4, 2],
      edges: [...edges].reverse(),
      viewerId: 1,
    })
    expect(shuffled).not.toBeNull()
    expect(shuffled).toEqual(forward)
  })

  it('空节点集合返回空布局', () => {
    expect(computeFamilyTreeLayout({ userIds: [], edges: [], viewerId: null })).toEqual(new Map())
  })
})
