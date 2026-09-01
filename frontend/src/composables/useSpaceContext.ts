import { useRouter } from 'vue-router'

import { useActionCardsStore } from '@/stores/actionCards'
import { useAgentStore } from '@/stores/agent'
import { useAuthStore } from '@/stores/auth'
import { useHouseholdCardStore } from '@/stores/household'
import { useKinshipStore } from '@/stores/kinship'
import { useMemoryStore } from '@/stores/memory'
import { useNotificationsStore } from '@/stores/notifications'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpaceStatsStore } from '@/stores/spaceStats'
import { useSpacesStore } from '@/stores/spaces'
import { useUiStore } from '@/stores/ui'
import type { FamilySpace } from '@/types/api'

/**
 * 空间上下文协调器（design.md §2.1/§3.2，09-01 Phase 2）。
 *
 * 职责边界：
 * - 默认空间选择：最近使用的 household > 用户 own/managed 的 household >
 *   第一个可用 household > 第一个 lineage；完全没有空间时返回 'none'，
 *   由调用方进入现有创建空间引导（绝不静默创建空间）；
 * - 空间切换事务（固定顺序，见 switchSpace）：
 *   1. 校验目标空间存在于服务端空间列表；
 *   2. 先递增切换 epoch 并更新 currentSpaceId（旧请求结果随即失效）；
 *   3. 清理旧空间敏感缓存（PersonalFamilyView、household、spaceStats、
 *      notifications、memory/RAG、ActionCard、kinship 关系详情、Assistant context）；
 *   4. 按空间类型加载新的服务端投影（失败进入新上下文的安全失败态，不回滚）；
 *   5. 重算当前空间管理员入口（spaces store getters 响应式重算）与默认页面目标，
 *      并导航到新空间类型的默认页；
 * - 最近使用空间只作为内存 UI 偏好（ui store），不把授权事实、人员、
 *   root/viewer 持久化到 localStorage；
 * - 登出 / 401 的全量清理仍由 auth.clearSession 负责，本模块只处理空间切换。
 */

export type SpaceKind = FamilySpace['kind']

export type SpaceContextKind = SpaceKind | 'none'

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
 * 默认空间选择（纯函数，可单测）。输入空间列表必须来自服务端
 * `GET /spaces` 投影；同优先级按服务端列表顺序取第一个，保证确定性。
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
export function defaultTargetForKind(kind: SpaceKind): { name: 'home' | 'family-space' } {
  return kind === 'household' ? { name: 'home' } : { name: 'family-space' }
}

/** 模块级切换代际：并发调用 switchSpace 时，旧事务的后续步骤全部丢弃 */
let switchEpoch = 0

/**
 * 空间上下文协调 composable。依赖 router（切换后导航到默认页面目标），
 * 只能在组件 setup 上下文调用；纯函数部分（selectDefaultSpaceId /
 * defaultTargetForKind）可独立单测。
 */
export function useSpaceContext() {
  const router = useRouter()
  const spaces = useSpacesStore()
  const auth = useAuthStore()
  const ui = useUiStore()

  function isAdminOf(spaceId: number): boolean {
    const userId = auth.user?.id
    if (userId === undefined) return false
    return spaces.members.some(
      (member) =>
        member.space_id === spaceId &&
        member.user_id === userId &&
        member.status === 'active' &&
        member.role === 'space_admin',
    )
  }

  /** 当前空间的默认页面目标；无空间时回退家庭默认页（路由守卫兜底）。 */
  function defaultTarget(): { name: 'home' | 'family-space' } {
    const kind = spaces.currentSpace?.kind
    return kind ? defaultTargetForKind(kind) : { name: 'home' }
  }

  /**
   * 清理旧空间敏感缓存（design.md §3.2 步骤 3）。各 store 自身的
   * epoch/generation 由此递增，旧空间的迟到响应一律不得回写。
   */
  function clearSpaceCaches(spaceId: number | null): void {
    if (spaceId === null) return
    usePersonalFamilyViewStore().clearSpace(spaceId)
    useHouseholdCardStore().clearSpace(spaceId)
    useSpaceStatsStore().clearSpace(spaceId)
    useNotificationsStore().clearSpace(spaceId)
    // memory/RAG 的空间分区（共享记忆与检索引用）；private 记忆是账号级，不清
    useMemoryStore().resetForSpace(spaceId)
    // ActionCard 分区与 kinship 关系详情缓存
    useActionCardsStore().resetForSpace(spaceId)
    useKinshipStore().resetForSpace(spaceId)
    // Assistant 会话上下文占位：关流并清空该空间分区（Agent Runtime 任务对齐边界）
    useAgentStore().resetForSpace(spaceId)
  }

  /**
   * 空间切换事务。成功返回 true；目标不存在或事务被更新的切换取代时返回 false。
   * 投影加载失败不抛出：新上下文进入各 store 的安全失败态，绝不回滚显示旧空间数据。
   */
  async function switchSpace(
    spaceId: number,
    options: { navigate?: boolean } = {},
  ): Promise<boolean> {
    // 1. 校验目标空间在服务端列表（列表为空时补拉一次）
    if (spaces.spaces.length === 0) {
      await spaces.load().catch(() => undefined)
    }
    const target = spaces.spaces.find((space) => space.id === spaceId)
    if (!target) return false
    if (spaces.currentSpaceId === spaceId) return true

    const previousSpaceId = spaces.currentSpaceId

    // 2. 先递增 epoch / 更新 context：此后旧空间的迟到响应全部失效
    switchEpoch += 1
    const epoch = switchEpoch
    spaces.currentSpaceId = spaceId

    // 3. 清理旧空间敏感缓存
    clearSpaceCaches(previousSpaceId)

    // 会话内最近使用的 household（仅内存 UI 偏好）
    if (target.kind === 'household') ui.setRecentHousehold(spaceId)

    // 4. 按空间类型加载新的服务端投影；失败留在新上下文的安全失败态。
    //    成员关系是空间授权上下文的基础投影，先行加载。
    await spaces.loadMembers(spaceId).catch(() => undefined)
    if (epoch !== switchEpoch) return false
    const primaryProjection =
      target.kind === 'household'
        ? useHouseholdCardStore().load(spaceId)
        : usePersonalFamilyViewStore().load(spaceId)
    await primaryProjection.catch(() => undefined)
    if (epoch !== switchEpoch) return false
    // 通知徽标投影：端点未落地（404）时安静降级，不抛错、无横幅
    await useNotificationsStore().load(spaceId).catch(() => undefined)
    if (epoch !== switchEpoch) return false

    // 5. 重算管理员入口（spaces getters 响应式生效）与默认页面目标
    if (options.navigate !== false) {
      await router.replace(defaultTargetForKind(target.kind)).catch(() => undefined)
    }
    return true
  }

  /**
   * 会话首次进入（登录 / 硬刷新）后的默认空间选择。
   * - 返回 'none'：完全没有空间，调用方走现有创建空间引导（不静默创建）；
   * - `defaultRouteFallback`：当前停留在登录默认页（name='home'）而默认空间是
   *   lineage 时，按 design.md §2.1 改入该 lineage 的家族树。
   */
  async function ensureDefaultSpace(
    options: { defaultRouteFallback?: boolean } = {},
  ): Promise<SpaceContextKind> {
    if (spaces.spaces.length === 0) {
      await spaces.load().catch(() => undefined)
    }
    const defaultId = selectDefaultSpaceId(spaces.spaces, {
      userId: auth.user?.id ?? null,
      recentHouseholdId: ui.recentHouseholdId,
      isAdminOf,
    })
    if (defaultId === null) return 'none'

    if (spaces.currentSpaceId !== defaultId) {
      const applied = await switchSpace(defaultId, { navigate: false })
      if (!applied) return 'none'
    }

    // 登录默认目标重算：停在 `home` 而默认空间是 lineage → 家族树
    if (
      options.defaultRouteFallback &&
      router.currentRoute.value.name === 'home' &&
      defaultTarget().name !== 'home'
    ) {
      await router.replace(defaultTarget()).catch(() => undefined)
    }
    return spaces.currentSpace?.kind ?? 'none'
  }

  return {
    defaultTarget,
    ensureDefaultSpace,
    switchSpace,
  }
}
