/**
 * 空间选择纯逻辑（09-20 启动期单点决策）。
 *
 * 为什么单独成模块：默认空间与「家族 → household/lineage 落点」原先分散在
 * spaces store 的 getter、useSpaceContext 的默认选择、以及 AppShell 的家族选项
 * 构造里，三处各写一份推断。启动期因此出现多个写入者竞争 currentSpaceId，
 * 表现为「先显示一个空间、再跳到另一个」的可见跳变。
 *
 * 本模块只做纯计算：输入服务端 `GET /spaces` 投影，输出空间 id。
 * 不读 store、不碰 router、不发请求，可独立单测。
 */

import type { FamilySpace } from '@/types/api'

/** 空间选择输入：全部来自服务端投影与会话内 UI 偏好。 */
export interface SelectDefaultSpaceOptions {
  /** 当前登录用户 id；未登录时为 null（无 own/managed 判定） */
  userId: number | null
  /** 会话内最近使用的 household（ui store 内存偏好） */
  recentHouseholdId: number | null
  /**
   * 目标空间是否持有 active space_admin 成员关系。成员数据按空间懒加载，
   * 未加载成员关系的空间在此返回 false（不做任何本地猜测）。
   */
  isAdminOf?: (spaceId: number) => boolean
}

/**
 * 默认空间选择（纯函数）。输入空间列表必须来自服务端 `GET /spaces` 投影；
 * 同优先级按服务端列表顺序取第一个，保证确定性。
 *
 * 优先级：会话内最近 household > own/managed household > 第一个 household
 * > 第一个 lineage。**不取列表第一个**——服务端按 created_at 排序，最新加入的
 * 空间不代表用户想先看到它。
 */
export function selectDefaultSpaceId(
  spaces: readonly FamilySpace[],
  options: SelectDefaultSpaceOptions,
): number | null {
  if (spaces.length === 0) return null

  const householdsWith = (predicate: (space: FamilySpace) => boolean): FamilySpace[] =>
    spaces.filter((space) => space.kind === 'household' && predicate(space))

  // 1. 会话内最近使用的 household（必须仍是服务端列表中的 household）
  if (options.recentHouseholdId !== null) {
    const recent = householdsWith((space) => space.id === options.recentHouseholdId)
    if (recent.length > 0) return recent[0]!.id
  }

  // 2. 用户 own（owner）或 managed（active space_admin）的 household
  const ownOrManaged = householdsWith(
    (space) =>
      (options.userId !== null && space.owner_id === options.userId) ||
      (options.isAdminOf?.(space.id) ?? false),
  )
  if (ownOrManaged.length > 0) return ownOrManaged[0]!.id

  // 3. 第一个可用 household
  const firstHousehold = spaces.find((space) => space.kind === 'household')
  if (firstHousehold) return firstHousehold.id

  // 4. 第一个 lineage
  const firstLineage = spaces.find((space) => space.kind === 'lineage')
  if (firstLineage) return firstLineage.id

  return null
}

/** 空间类型 → 默认页面目标（design.md §1.1：两种页面语义） */
export function defaultTargetForKind(kind: FamilySpace['kind']): {
  name: 'home' | 'family-space'
} {
  return kind === 'household' ? { name: 'home' } : { name: 'family-space' }
}

/**
 * household → 所属 lineage（纯函数，store getter 与启动决策共用同一份判定）。
 *
 * 显式 `lineage_space_id` 配对优先；旧数据回退「owner 唯一对应一个 lineage」的
 * 确定性推断，多候选不猜（返回 null）。显式配对存在但目标不在当前授权投影时
 * 也不回退 owner 猜测——避免把当前家庭错误显示到另一家族空间。
 */
export function lineageForSpace(
  spaces: readonly FamilySpace[],
  spaceId: number,
): FamilySpace | null {
  const space = spaces.find((s) => s.id === spaceId && s.kind === 'household')
  if (!space) return null
  const linkedId = space.lineage_space_id ?? null
  if (linkedId !== null) {
    return spaces.find((s) => s.id === linkedId && s.kind === 'lineage') ?? null
  }
  const owned = spaces.filter((s) => s.kind === 'lineage' && s.owner_id === space.owner_id)
  return owned.length === 1 ? owned[0]! : null
}

/**
 * lineage → 落点 household（纯函数）。显式配对优先；无显式配对时回退
 * 「owner 相等且未挂到其他家族」的确定性推断。多候选时依次取：当前空间
 * （已在该家族内）→ 本人 own 的 → 列表第一个，保证切换总能确定性落位。
 */
export function householdForLineage(
  spaces: readonly FamilySpace[],
  lineageId: number,
  options: { currentSpaceId?: number | null; userId?: number | null } = {},
): FamilySpace | null {
  const lineage = spaces.find((s) => s.id === lineageId && s.kind === 'lineage')
  if (!lineage) return null
  let candidates = spaces.filter(
    (s) => s.kind === 'household' && (s.lineage_space_id ?? null) === lineageId,
  )
  if (candidates.length === 0) {
    candidates = spaces.filter(
      (s) =>
        s.kind === 'household' &&
        (s.lineage_space_id ?? null) === null &&
        s.owner_id === lineage.owner_id,
    )
  }
  if (candidates.length === 0) return null
  const current = candidates.find((s) => s.id === options.currentSpaceId)
  if (current) return current
  const userId = options.userId ?? null
  const own = userId === null ? [] : candidates.filter((s) => s.owner_id === userId)
  return (own.length > 0 ? own : candidates)[0]!
}

/** 一个家族在选择器里占一项：lineage 是主身份，配对 household 落在同一项内。 */
export interface FamilySpaceGroup {
  /** 选择器选项值：有 lineage 时取 lineage id，孤立 household 取自身 id */
  value: number
  label: string
  lineageId: number | null
  householdId: number | null
  /** 该家族项包含的全部 household id（含孤立 household 自身） */
  householdIds: number[]
}

/**
 * 家族分组（纯函数）：lineage 与其配对 household 合成一项；解析不到所属 lineage
 * 的孤立 household 保留为独立项，保证它不会从切换器消失。
 */
export function buildFamilyGroups(spaces: readonly FamilySpace[]): FamilySpaceGroup[] {
  const lineages = spaces.filter((space) => space.kind === 'lineage')
  const lineageGroups: FamilySpaceGroup[] = lineages.map((lineage) => {
    const paired = householdForLineage(spaces, lineage.id)
    return {
      value: lineage.id,
      label: lineage.name,
      lineageId: lineage.id,
      householdId: paired?.id ?? null,
      householdIds: spaces
        .filter(
          (space) => space.kind === 'household' && lineageForSpace(spaces, space.id)?.id === lineage.id,
        )
        .map((space) => space.id),
    }
  })
  const orphanGroups: FamilySpaceGroup[] = spaces
    .filter((space) => space.kind === 'household' && lineageForSpace(spaces, space.id) === null)
    .map((space) => ({
      value: space.id,
      label: space.name,
      lineageId: null,
      householdId: space.id,
      householdIds: [space.id],
    }))
  return [...lineageGroups, ...orphanGroups]
}

/**
 * 启动期最终空间：先按优先级定「当前家族」，再按当前路由选该家族内的落点。
 *
 * 这是启动期**唯一**的决策点——它一次给出最终 id，调用方只切换一次，
 * 不再出现「先 household 再 lineage」的中间态。
 *
 * - `family-space`（家族树）→ 该家族的 lineage；
 * - `home`（家庭卡）→ 该家族的 household；
 * - 其它路由（设置/统计/记忆等）→ 优先级选出的空间本身，保持「非空间页不跳页」；
 * - 该家族缺少所需类型时退回该家族可用空间（例如未配对的孤立 household 停在
 *   家族树页 → 仍用该 household，由既有提示面板说明），不反复重试。
 */
export function resolveStartupSpaceId(
  spaces: readonly FamilySpace[],
  options: SelectDefaultSpaceOptions,
  routeName: string | symbol | null | undefined,
): number | null {
  const defaultId = selectDefaultSpaceId(spaces, options)
  if (defaultId === null) return null
  if (routeName !== 'family-space' && routeName !== 'home') return defaultId

  const defaultSpace = spaces.find((space) => space.id === defaultId)
  if (!defaultSpace) return defaultId

  const wantLineage = routeName === 'family-space'
  if (wantLineage) {
    if (defaultSpace.kind === 'lineage') return defaultSpace.id
    const lineage = lineageForSpace(spaces, defaultSpace.id)
    return lineage?.id ?? defaultSpace.id
  }
  if (defaultSpace.kind === 'household') return defaultSpace.id
  const household = householdForLineage(spaces, defaultSpace.id, {
    currentSpaceId: defaultId,
    userId: options.userId,
  })
  return household?.id ?? defaultSpace.id
}
