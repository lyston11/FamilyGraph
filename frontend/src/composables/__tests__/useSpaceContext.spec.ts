import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  defaultTargetForKind,
  selectDefaultSpaceId,
  useSpaceContext,
} from '@/composables/useSpaceContext'
import type { FamilySpace, HouseholdCardSnapshot, SpaceMemberInfo } from '@/types/api'
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
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import { useUiStore } from '@/stores/ui'

// 空间切换事务按空间类型触发新投影加载；API 层全部打桩。
vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn(),
  fetchSpaceMembers: vi.fn(),
  fetchOwnershipTransfers: vi.fn(),
  fetchSpaceProfileRefs: vi.fn(),
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

vi.mock('@/api/household', () => ({ fetchHouseholdCard: vi.fn() }))
vi.mock('@/api/personalFamilyView', () => ({ fetchPersonalFamilyView: vi.fn() }))
vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}))

const spacesApi = await import('@/api/spaces')
const householdApi = await import('@/api/household')
const pfvApi = await import('@/api/personalFamilyView')
const notificationsApi = await import('@/api/notifications')

const fetchSpacesMock = vi.mocked(spacesApi.fetchSpaces)
const fetchSpaceMembersMock = vi.mocked(spacesApi.fetchSpaceMembers)
const createSpaceMock = vi.mocked(spacesApi.createSpace)
const fetchHouseholdCardMock = vi.mocked(householdApi.fetchHouseholdCard)
const fetchPersonalFamilyViewMock = vi.mocked(pfvApi.fetchPersonalFamilyView)
const fetchNotificationsMock = vi.mocked(notificationsApi.fetchNotifications)

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

function makeMember(overrides: Partial<SpaceMemberInfo>): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 7,
    user_id: 1,
    added_by: 1,
    role: 'member',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
    ...overrides,
  }
}

describe('selectDefaultSpaceId（默认空间优先级）', () => {
  const base = {
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 1,
  }

  it('优先级 1：会话内最近使用的 household 最优先', () => {
    const spaces = [
      makeSpace({ id: 7, owner_id: 1, kind: 'household', ...base }),
      makeSpace({ id: 8, name: '父母家', owner_id: 9, kind: 'household', ...base }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: 8 })).toBe(8)
  })

  it('优先级 1 失效：最近空间不在服务端列表时按后续规则回落', () => {
    const spaces = [makeSpace({ id: 7, owner_id: 1, kind: 'household', ...base })]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: 99 })).toBe(7)
    // 最近偏好指向 lineage 时无效（只记录 household）
    const withLineage = [
      makeSpace({ id: 7, owner_id: 1, kind: 'household', ...base }),
      makeSpace({ id: 12, name: '家族', owner_id: 9, kind: 'lineage', ...base }),
    ]
    expect(selectDefaultSpaceId(withLineage, { userId: 1, recentHouseholdId: 12 })).toBe(7)
  })

  it('优先级 2：own（owner_id）或 managed（active space_admin）的 household', () => {
    const spaces = [
      makeSpace({ id: 7, owner_id: 9, kind: 'household', ...base }),
      makeSpace({ id: 8, owner_id: 1, kind: 'household', ...base }),
      makeSpace({ id: 9, owner_id: 9, kind: 'household', ...base }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: null })).toBe(8)
    // 非 owner 但在成员关系中持有 space_admin（managed）
    expect(
      selectDefaultSpaceId(spaces, {
        userId: 1,
        recentHouseholdId: null,
        isAdminOf: (id) => id === 7,
      }),
    ).toBe(7)
  })

  it('优先级 3：无 recent/own/managed 时取第一个 household', () => {
    const spaces = [
      makeSpace({ id: 7, owner_id: 9, kind: 'household', ...base }),
      makeSpace({ id: 8, owner_id: 9, kind: 'household', ...base }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: null })).toBe(7)
  })

  it('优先级 4：没有 household 时取第一个 lineage', () => {
    const spaces = [
      makeSpace({ id: 12, name: '家族', owner_id: 9, kind: 'lineage', ...base }),
      makeSpace({ id: 13, name: '宗族', owner_id: 9, kind: 'lineage', ...base }),
    ]
    expect(selectDefaultSpaceId(spaces, { userId: 1, recentHouseholdId: null })).toBe(12)
  })

  it('完全没有空间时返回 null（走现有创建空间引导，不静默创建）', () => {
    expect(selectDefaultSpaceId([], { userId: 1, recentHouseholdId: null })).toBeNull()
  })

  it('默认页面目标按空间类型重算', () => {
    expect(defaultTargetForKind('household')).toEqual({ name: 'home' })
    expect(defaultTargetForKind('lineage')).toEqual({ name: 'family-space' })
  })
})

describe('useSpaceContext（空间切换事务）', () => {
  let pinia: Pinia

  async function mountHarness() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div />' } },
        { path: '/family-tree', name: 'family-space', component: { template: '<div />' } },
        { path: '/login', name: 'login', component: { template: '<div />' } },
      ],
    })
    let ctx: ReturnType<typeof useSpaceContext> | null = null
    const Harness = defineComponent({
      setup() {
        ctx = useSpaceContext()
        return () => null
      },
    })
    const wrapper = mount(Harness, { global: { plugins: [pinia, router] } })
    await flushPromises()
    // ctx 在子组件 setup 内赋值；TS 无法跨闭包追踪，这里显式收窄
    const spaceCtx = ctx as ReturnType<typeof useSpaceContext> | null
    if (spaceCtx === null) throw new Error('harness did not initialize')
    return { wrapper, ctx: spaceCtx, router }
  }

  function seedLoggedIn(): void {
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      name: '张三',
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    }
  }

  beforeEach(() => {
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    fetchSpacesMock.mockResolvedValue([])
    fetchSpaceMembersMock.mockResolvedValue([])
    vi.mocked(spacesApi.fetchOwnershipTransfers).mockResolvedValue([])
    vi.mocked(spacesApi.fetchSpaceProfileRefs).mockResolvedValue([])
    fetchHouseholdCardMock.mockResolvedValue(null)
    fetchPersonalFamilyViewMock.mockResolvedValue(null)
    fetchNotificationsMock.mockResolvedValue(null)
    seedLoggedIn()
  })

  it('事务顺序：校验 → 先切换 context → 再清理旧空间缓存 → 加载新投影 → 导航默认页', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, kind: 'household' }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    const { wrapper, ctx, router } = await mountHarness()
    const spaces = useSpacesStore()
    await spaces.load()
    expect(spaces.currentSpaceId).toBe(7)

    const household = useHouseholdCardStore()
    const pfv = usePersonalFamilyViewStore()
    const spaceStats = useSpaceStatsStore()
    const notifications = useNotificationsStore()
    const memory = useMemoryStore()
    const actionCards = useActionCardsStore()
    const kinship = useKinshipStore()
    const agent = useAgentStore()

    // 观察清理时机：清理旧空间时当前上下文必须已经指向新空间
    let contextAtClear: number | null = null
    const originalClear = pfv.clearSpace.bind(pfv)
    const pfvSpy = vi.spyOn(pfv, 'clearSpace').mockImplementation((spaceId: number) => {
      contextAtClear = spaces.currentSpaceId
      originalClear(spaceId)
    })
    const clearSpies = [
      pfvSpy,
      vi.spyOn(household, 'clearSpace'),
      vi.spyOn(spaceStats, 'clearSpace'),
      vi.spyOn(notifications, 'clearSpace'),
      vi.spyOn(useStewardSuggestionsStore(), 'clearSpace'),
      vi.spyOn(memory, 'resetForSpace'),
      vi.spyOn(actionCards, 'resetForSpace'),
      vi.spyOn(kinship, 'resetForSpace'),
      vi.spyOn(agent, 'resetForSpace'),
    ]

    const applied = await ctx.switchSpace(12)
    expect(applied).toBe(true)

    // 全部敏感 store 都清了旧空间（7），且清理发生在 context 已切换之后
    for (const spy of clearSpies) {
      expect(spy).toHaveBeenCalledWith(7)
    }
    expect(contextAtClear).toBe(12)
    for (const spy of clearSpies) {
      expect(spy).not.toHaveBeenCalledWith(12)
    }

    // 按空间类型加载新投影：lineage → PersonalFamilyView；household 投影不加载
    expect(fetchPersonalFamilyViewMock).toHaveBeenCalledWith(12, null)
    expect(fetchHouseholdCardMock).not.toHaveBeenCalled()

    // 管理员入口与默认页面目标按新空间类型重算，并导航到家族树
    expect(router.currentRoute.value.name).toBe('family-space')
    expect(ctx.defaultTarget()).toEqual({ name: 'family-space' })
    wrapper.unmount()
  })

  it('目标不在服务端空间列表时拒绝切换（fail-closed，不改任何状态）', async () => {
    fetchSpacesMock.mockResolvedValue([makeSpace({ id: 7, kind: 'household' })])
    const { ctx, router } = await mountHarness()
    const spaces = useSpacesStore()
    await spaces.load()

    const applied = await ctx.switchSpace(999)
    expect(applied).toBe(false)
    expect(spaces.currentSpaceId).toBe(7)
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('旧空间在途请求晚到：被清理 epoch 丢弃，不回写旧空间数据', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, kind: 'household' }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    const { ctx } = await mountHarness()
    const spaces = useSpacesStore()
    await spaces.load()

    const household = useHouseholdCardStore()
    let resolveStale!: (value: HouseholdCardSnapshot | null) => void
    fetchHouseholdCardMock.mockImplementationOnce(
      () => new Promise<HouseholdCardSnapshot | null>((resolve) => resolveStale = resolve),
    )
    // 旧空间（7）的在途请求先发出
    const pendingLoad = household.load(7)
    expect(fetchHouseholdCardMock).toHaveBeenCalledWith(7, null)

    // 切换空间：7 的缓存被清理、epoch 递增
    await ctx.switchSpace(12)

    // 迟到的旧空间响应：不回写
    resolveStale({
      data: {
        space_id: 7,
        space_kind: 'household',
        space_name: '我的家庭',
        view_version: 3,
        computed_at: '2026-09-01T00:00:00',
        viewer: makeDisplay(1),
        members: [],
        allowed_actions: { can_invite_members: false, can_create_household: false, empty_state_hint: null },
      },
      etag: null,
    })
    await pendingLoad
    expect(household.forSpace(7)).toBeNull()

    // 新上下文按 lineage 类型加载
    expect(fetchPersonalFamilyViewMock).toHaveBeenCalledWith(12, null)
  })

  it('新空间投影加载失败：保留新上下文的安全失败态，不回滚显示旧空间数据', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, kind: 'household' }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    // 第 1 次成员请求（初始 spaces.load）正常；第 2 次（切换到 12）失败
    fetchSpaceMembersMock
      .mockResolvedValueOnce([])
      .mockRejectedValueOnce(new Error('members endpoint down'))
    const { ctx, router } = await mountHarness()
    const spaces = useSpacesStore()
    await spaces.load()
    spaces.members = [makeMember({ space_id: 7, role: 'space_admin' })]

    const applied = await ctx.switchSpace(12)
    expect(applied).toBe(true)
    // 上下文已指向新空间；旧空间成员关系不残留（不会闪现旧授权数据）
    expect(spaces.currentSpaceId).toBe(12)
    expect(spaces.members).toEqual([])
    // 失败仍重算默认目标并导航
    expect(router.currentRoute.value.name).toBe('family-space')
  })

  it('切换回 household 空间时默认目标回到「我的家庭」', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, kind: 'household' }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    const { ctx, router } = await mountHarness()
    const spaces = useSpacesStore()
    await spaces.load()

    await ctx.switchSpace(12)
    expect(router.currentRoute.value.name).toBe('family-space')

    await ctx.switchSpace(7)
    expect(router.currentRoute.value.name).toBe('home')
    expect(fetchHouseholdCardMock).toHaveBeenCalledWith(7, null)
  })
})

describe('useSpaceContext（默认空间选择）', () => {
  let pinia: Pinia

  async function mountHarness() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'home', component: { template: '<div />' } },
        { path: '/family-tree', name: 'family-space', component: { template: '<div />' } },
        { path: '/login', name: 'login', component: { template: '<div />' } },
      ],
    })
    let ctx: ReturnType<typeof useSpaceContext> | null = null
    const Harness = defineComponent({
      setup() {
        ctx = useSpaceContext()
        return () => null
      },
    })
    const wrapper = mount(Harness, { global: { plugins: [pinia, router] } })
    await flushPromises()
    // ctx 在子组件 setup 内赋值；TS 无法跨闭包追踪，这里显式收窄
    const spaceCtx = ctx as ReturnType<typeof useSpaceContext> | null
    if (spaceCtx === null) throw new Error('harness did not initialize')
    return { wrapper, ctx: spaceCtx, router }
  }

  beforeEach(() => {
    localStorage.clear()
    pinia = createPinia()
    setActivePinia(pinia)
    vi.clearAllMocks()
    fetchSpaceMembersMock.mockResolvedValue([])
    vi.mocked(spacesApi.fetchOwnershipTransfers).mockResolvedValue([])
    vi.mocked(spacesApi.fetchSpaceProfileRefs).mockResolvedValue([])
    fetchHouseholdCardMock.mockResolvedValue(null)
    fetchPersonalFamilyViewMock.mockResolvedValue(null)
    fetchNotificationsMock.mockResolvedValue(null)
    const auth = useAuthStore()
    auth.user = {
      id: 1,
      name: '张三',
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    }
  })

  it('完全没有空间：返回 none，走现有创建空间引导，绝不静默创建', async () => {
    fetchSpacesMock.mockResolvedValue([])
    const { ctx, router } = await mountHarness()

    const kind = await ctx.ensureDefaultSpace({ defaultRouteFallback: true })
    expect(kind).toBe('none')
    expect(useSpacesStore().currentSpaceId).toBeNull()
    expect(createSpaceMock).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('home')
  })

  it('有 household（own）时默认进入该 household', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, owner_id: 1, kind: 'household' }),
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    const { ctx } = await mountHarness()

    const kind = await ctx.ensureDefaultSpace()
    expect(kind).toBe('household')
    const spaces = useSpacesStore()
    expect(spaces.currentSpaceId).toBe(7)
    expect(fetchSpaceMembersMock).toHaveBeenCalledWith(7)
  })

  it('只有 lineage 时默认进入该 lineage 的家族树', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 12, name: '张氏家族', owner_id: 9, kind: 'lineage' }),
    ])
    const { ctx, router } = await mountHarness()
    await router.push('/')

    const kind = await ctx.ensureDefaultSpace({ defaultRouteFallback: true })
    expect(kind).toBe('lineage')
    expect(useSpacesStore().currentSpaceId).toBe(12)
    // 登录默认页重算：home + lineage → 家族树
    expect(router.currentRoute.value.name).toBe('family-space')
  })

  it('会话内最近使用的 household 参与默认选择（仅内存偏好）', async () => {
    fetchSpacesMock.mockResolvedValue([
      makeSpace({ id: 7, owner_id: 1, kind: 'household' }),
      makeSpace({ id: 8, name: '父母家', owner_id: 9, kind: 'household' }),
    ])
    const { ctx } = await mountHarness()
    const ui = useUiStore()
    ui.setRecentHousehold(8)

    const kind = await ctx.ensureDefaultSpace()
    expect(kind).toBe('household')
    expect(useSpacesStore().currentSpaceId).toBe(8)
  })

  it('最近偏好的空间已不在服务端列表时回落到 own household', async () => {
    fetchSpacesMock.mockResolvedValue([makeSpace({ id: 7, owner_id: 1, kind: 'household' })])
    const { ctx } = await mountHarness()
    useUiStore().setRecentHousehold(99)

    await ctx.ensureDefaultSpace()
    expect(useSpacesStore().currentSpaceId).toBe(7)
  })
})

// ---- 测试夹具 ----

function makeDisplay(id: number): import('@/types/api').PersonalFamilyViewDisplay {
  const masked = { __masked__: true } as const
  return {
    id,
    name: `成员${id}`,
    gender: masked,
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: masked,
    claim_status: masked,
  }
}
