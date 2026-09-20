/**
 * 启动期空间选择纯逻辑（09-20）。
 *
 * 覆盖：默认空间优先级（不受服务端 created_at 排序影响）、家族分组、
 * 按当前路由解析落点（家族树 → lineage / 家庭卡 → household）、
 * 缺所需类型时退回该家族可用空间、未配对孤立 household。
 */

import { describe, expect, it } from 'vitest'

import {
  buildFamilyGroups,
  defaultTargetForKind,
  householdForLineage,
  lineageForSpace,
  resolveStartupSpaceId,
  selectDefaultSpaceId,
} from '@/composables/spaceSelection'
import type { FamilySpace } from '@/types/api'

function makeSpace(overrides: Partial<FamilySpace>): FamilySpace {
  return {
    id: 7,
    name: '我的家庭',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 1,
    ...overrides,
  }
}

/**
 * 合成场景（非种子实况）：我在别人拥有的 household「李家」里，自己的是「明皇室」，
 * 且服务端按 created_at 排序时「李家」在前。用来验证默认空间不取列表首项。
 */
function zhuScene(): FamilySpace[] {
  return [
    makeSpace({ id: 19, name: '李家', owner_id: 48, kind: 'household', lineage_space_id: 20 }),
    makeSpace({ id: 1, name: '明皇室', owner_id: 1, kind: 'household', lineage_space_id: 2 }),
    makeSpace({ id: 2, name: '朱氏皇族', owner_id: 1, kind: 'lineage' }),
    makeSpace({ id: 3, name: '马府', owner_id: 2, kind: 'household', lineage_space_id: 4 }),
    makeSpace({ id: 4, name: '马氏家族', owner_id: 2, kind: 'lineage' }),
    makeSpace({ id: 20, name: '李氏家族', owner_id: 48, kind: 'lineage' }),
  ]
}

describe('selectDefaultSpaceId（默认空间优先级）', () => {
  it('优先级 1：会话内最近使用的 household 最优先', () => {
    const spaces = [
      makeSpace({ id: 7, owner_id: 1 }),
      makeSpace({ id: 8, name: '父母家', owner_id: 9 }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: 8 })).toBe(8)
  })

  it('优先级 1 失效：最近空间不在服务端列表时按后续规则回落', () => {
    const spaces = [makeSpace({ id: 7, owner_id: 1 })]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: 99 })).toBe(7)
    // 最近偏好指向 lineage 时无效（只记录 household）
    const withLineage = [
      makeSpace({ id: 7, owner_id: 1 }),
      makeSpace({ id: 12, name: '家族', owner_id: 9, kind: 'lineage' }),
    ]
    expect(selectDefaultSpaceId(withLineage, { userId: 1, recentHouseholdId: 12 })).toBe(7)
  })

  it('优先级 2：own（owner_id）或 managed（active space_admin）的 household', () => {
    const spaces = [
      makeSpace({ id: 7, owner_id: 9 }),
      makeSpace({ id: 8, owner_id: 1 }),
      makeSpace({ id: 9, owner_id: 9 }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: null })).toBe(8)
    expect(
      selectDefaultSpaceId(spaces, {
        userId: 1,
        recentHouseholdId: null,
        isAdminOf: (id) => id === 7,
      }),
    ).toBe(7)
  })

  it('优先级 3/4 与空列表', () => {
    const households = [
      makeSpace({ id: 7, owner_id: 9 }),
      makeSpace({ id: 8, owner_id: 9 }),
    ]
    expect(selectDefaultSpaceId(households, { userId: 1, recentHouseholdId: null })).toBe(7)

    const lineages = [
      makeSpace({ id: 12, name: '家族', owner_id: 9, kind: 'lineage' }),
      makeSpace({ id: 13, name: '宗族', owner_id: 9, kind: 'lineage' }),
    ]
    expect(selectDefaultSpaceId(lineages, { userId: 1, recentHouseholdId: null })).toBe(12)
    expect(selectDefaultSpaceId([], { userId: 1, recentHouseholdId: null })).toBeNull()
  })

  it('不受服务端 created_at 排序影响：不取列表第一个空间', () => {
    const spaces = zhuScene()
    // 列表首项是别人拥有、且最新加入的「李家」，但本人 own 的是「明皇室」
    expect(spaces[0]!.id).toBe(19)
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: null })).toBe(1)
  })

  it('默认页面目标按空间类型重算', () => {
    expect(defaultTargetForKind('household')).toEqual({ name: 'home' })
    expect(defaultTargetForKind('lineage')).toEqual({ name: 'family-space' })
  })
})

describe('resolveStartupSpaceId（启动期按路由一次到位）', () => {
  const options = { userId: 1, recentHouseholdId: null }

  it('家族树路由 → 该家族的 lineage（不落在 household 中间态）', () => {
    expect(resolveStartupSpaceId(zhuScene(), options, 'family-space')).toBe(2)
  })

  it('家庭卡路由 → 该家族的 household', () => {
    expect(resolveStartupSpaceId(zhuScene(), options, 'home')).toBe(1)
  })

  it('其它路由（设置/统计/记忆）→ 优先级选出的空间本身，不换类型', () => {
    for (const routeName of ['settings', 'stats', 'memory', 'person-profile']) {
      expect(resolveStartupSpaceId(zhuScene(), options, routeName)).toBe(1)
    }
  })

  it('最近使用的 household 参与家族判定：停在家族树时取其配对 lineage', () => {
    expect(
      resolveStartupSpaceId(zhuScene(), { userId: 1, recentHouseholdId: 3 }, 'family-space'),
    ).toBe(4)
  })

  it('未配对的孤立 household 停在家族树页 → 退回该 household（不反复重试）', () => {
    const spaces = [
      makeSpace({ id: 7, name: '我的家庭', owner_id: 1 }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ]
    // 解析不到所属 lineage（owner 不匹配任何 lineage）→ 保留 household 自身
    expect(resolveStartupSpaceId(spaces, options, 'family-space')).toBe(7)
  })

  it('默认空间本身是 lineage 时，家庭卡路由落到其配对 household', () => {
    const spaces = [
      makeSpace({ id: 12, name: '张氏家族', owner_id: 1, kind: 'lineage' }),
      makeSpace({ id: 7, name: '我的家庭', owner_id: 1, lineage_space_id: 12 }),
    ]
    // 无 household 时优先级会选 lineage（优先级 4）
    expect(selectDefaultSpaceId(spaces, options)).toBe(7)
    expect(resolveStartupSpaceId(spaces, options, 'home')).toBe(7)
  })

  it('完全没有空间时返回 null（走既有创建引导，不静默创建）', () => {
    expect(resolveStartupSpaceId([], options, 'family-space')).toBeNull()
  })
})

describe('家族分组与配对解析', () => {
  it('lineage 与其配对 household 合成一项；孤立 household 保留为独立项', () => {
    const spaces = [
      ...zhuScene(),
      makeSpace({ id: 30, name: '孤立家庭', owner_id: 77 }),
    ]
    const groups = buildFamilyGroups(spaces)
    const byValue = new Map(groups.map((group) => [group.value, group]))
    expect(byValue.get(2)!.householdId).toBe(1)
    expect(byValue.get(2)!.householdIds).toEqual([1])
    expect(byValue.get(20)!.householdId).toBe(19)
    // 孤立 household 仍出现在切换器里
    expect(byValue.get(30)!.lineageId).toBeNull()
    expect(byValue.get(30)!.householdId).toBe(30)
  })

  it('显式配对优先；无显式配对时按 owner 唯一匹配推断，多候选不猜', () => {
    const explicit = [
      makeSpace({ id: 7, name: '我的家庭', owner_id: 1, lineage_space_id: 12 }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ]
    expect(lineageForSpace(explicit, 7)?.id).toBe(12)
    expect(householdForLineage(explicit, 12)?.id).toBe(7)

    const inferred = [
      makeSpace({ id: 7, owner_id: 1 }),
      makeSpace({ id: 12, owner_id: 1, kind: 'lineage' }),
    ]
    expect(lineageForSpace(inferred, 7)?.id).toBe(12)

    const ambiguous = [
      makeSpace({ id: 7, owner_id: 1 }),
      makeSpace({ id: 12, owner_id: 1, kind: 'lineage' }),
      makeSpace({ id: 13, owner_id: 1, kind: 'lineage' }),
    ]
    expect(lineageForSpace(ambiguous, 7)).toBeNull()
  })
})
