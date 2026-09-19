import { defineStore } from 'pinia'

import { useAuthStore } from '@/stores/auth'
import type {
  FamilySpace,
  OwnershipTransfer,
  SpaceMemberInfo,
  SpaceProfileRefInfo,
} from '@/types/api'

import {
  createOwnershipTransfer,
  createSpace,
  fetchOwnershipTransfers,
  fetchSpaceMembers,
  fetchSpaceProfileRefs,
  fetchSpaces,
  inviteToSpace,
  removeOrWithdrawMembership,
  requestLineageAccess,
  resolveMembership,
  respondOwnershipTransfer,
  setSpaceLineageLink,
  updateSpace,
} from '@/api/spaces'

/** 家庭空间状态（m1c）。空列表时由首页引导创建家庭空间。 */
export const useSpacesStore = defineStore('spaces', {
  state: () => ({
    /**
     * 会话代际（非持久视图状态）：clear/登出后递增；异步响应回写前校验，
     * 旧会话/旧空间的迟到响应不得覆盖新状态（P2 隔离）。
     */
    generation: 0,
    spaces: [] as FamilySpace[],
    /** 当前查看的空间（默认优先级见 architecture §3） */
    currentSpaceId: null as number | null,
    members: [] as SpaceMemberInfo[],
    /** 当前空间的待确档最小引用（AC-F2；仅名字投影） */
    profileRefs: [] as SpaceProfileRefInfo[],
    /** 当前空间的 owner 移交记录（含历史；AC-F5） */
    transfers: [] as OwnershipTransfer[],
    membersError: null as string | null,
    loading: false,
  }),
  getters: {
    currentSpace(state): FamilySpace | null {
      return state.spaces.find((s) => s.id === state.currentSpaceId) ?? state.spaces[0] ?? null
    },
    /** 当前 active membership；不读取 custody/profile 字段，避免跨域混用。 */
    currentMembership(state): SpaceMemberInfo | null {
      const auth = useAuthStore()
      const userId = auth.user?.id
      const currentSpaceId = state.currentSpaceId
      if (userId === undefined || currentSpaceId === null) return null
      return (
        state.members.find(
          (member) =>
            member.space_id === currentSpaceId &&
            member.user_id === userId &&
            member.status === 'active',
        ) ?? null
      )
    },
    currentRole(): SpaceMemberInfo['role'] | null {
      return this.currentMembership?.role ?? this.currentSpace?.current_role ?? null
    },
    /**
     * 当前空间管理员：产品层唯一管理员角色，按当前 space_id 的成员关系判定。
     * 用户在其他空间的管理员身份不影响这里。
     */
    isSpaceAdmin(): boolean {
      return this.currentRole === 'space_admin'
    },
    canManageSpace(): boolean {
      return this.isSpaceAdmin
    },
    /** 邀请授权：当前空间 active 成员均可邀请；受邀人仍需本人接受。 */
    canInvite(): boolean {
      return this.currentMembership !== null
    },
    /** 交接由当前空间管理员发起（原 owner 移交入口，产品文案统一为管理员）。 */
    canTransferOwnership(): boolean {
      return this.isSpaceAdmin
    },
    /** 全部家族空间（「当前家族空间」选择器唯一选项来源，本 store 只含我 active 成员的空间）。 */
    lineageSpaces(state): FamilySpace[] {
      return state.spaces.filter((space) => space.kind === 'lineage')
    },
    /**
     * household → 所属 lineage：显式 lineage_space_id 优先；旧数据回退
     * 「owner 唯一对应一个 lineage」的确定性推断，多候选不猜（返回 null）。
     */
    lineageForSpace(state) {
      return (spaceId: number): FamilySpace | null => {
        const space = state.spaces.find((s) => s.id === spaceId && s.kind === 'household')
        if (!space) return null
        const linkedId = space.lineage_space_id ?? null
        if (linkedId !== null) {
          const linked = state.spaces.find((s) => s.id === linkedId && s.kind === 'lineage')
          // 显式链接存在但目标不在当前授权投影时，不回退 owner 猜测，避免
          // 把当前家庭错误显示到另一家族空间。
          return linked ?? null
        }
        const owned = state.spaces.filter(
          (s) => s.kind === 'lineage' && s.owner_id === space.owner_id,
        )
        return owned.length === 1 ? owned[0]! : null
      }
    },
    /**
     * lineage → 落点 household（家庭卡页的家族切换目标）：显式配对优先；
     * 无显式配对时回退「owner 相等且未挂到其他家族」的确定性推断。
     * 多候选时依次取：当前家庭卡（已在该家族内）→ 本人 own 的 → 服务端列表
     * 第一个，保证切换总能确定性落位。
     */
    householdForLineage(state) {
      return (lineageId: number): FamilySpace | null => {
        const lineage = state.spaces.find((s) => s.id === lineageId && s.kind === 'lineage')
        if (!lineage) return null
        let candidates = state.spaces.filter(
          (s) => s.kind === 'household' && (s.lineage_space_id ?? null) === lineageId,
        )
        if (candidates.length === 0) {
          candidates = state.spaces.filter(
            (s) =>
              s.kind === 'household' &&
              (s.lineage_space_id ?? null) === null &&
              s.owner_id === lineage.owner_id,
          )
        }
        if (candidates.length === 0) return null
        const current = candidates.find((s) => s.id === state.currentSpaceId)
        if (current) return current
        const userId = useAuthStore().user?.id
        const own = userId === undefined ? [] : candidates.filter((s) => s.owner_id === userId)
        return (own.length > 0 ? own : candidates)[0]!
      }
    },
    activeMembers(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'active')
    },
    pendingForMe(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'pending')
    },
    /** 当前空间的 pending 移交（含发起人与受让人视角） */
    pendingTransfers(state): OwnershipTransfer[] {
      return state.transfers.filter((t) => t.status === 'pending')
    },
  },
  actions: {
    async load() {
      const generation = this.generation
      this.loading = true
      try {
        const spaces = await fetchSpaces()
        if (generation !== this.generation) return
        this.spaces = spaces
        if (this.currentSpaceId === null && this.spaces.length > 0) {
          this.currentSpaceId = this.spaces[0].id
        }
        if (this.currentSpaceId !== null) await this.loadMembers(this.currentSpaceId)
        else this.members = []
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    async loadMembers(spaceId: number, options: { setCurrentSpace?: boolean } = {}): Promise<SpaceMemberInfo[]> {
      const generation = this.generation
      const setCurrentSpace = options.setCurrentSpace ?? true
      const previousSpaceId = this.currentSpaceId
      if (setCurrentSpace) this.currentSpaceId = spaceId
      if (setCurrentSpace && previousSpaceId !== null && previousSpaceId !== spaceId) {
        this.members = []
        this.transfers = []
        this.profileRefs = []
      }
      if (setCurrentSpace) this.membersError = null
      let loadedMembers: SpaceMemberInfo[] = []
      try {
        loadedMembers = await fetchSpaceMembers(spaceId)
        if (generation !== this.generation || (setCurrentSpace && this.currentSpaceId !== spaceId)) return []
        // 路由守卫只做目标空间授权预检：不得把目标成员投影写进当前空间上下文。
        if (!setCurrentSpace) return loadedMembers
        this.members = loadedMembers
      } catch (error) {
        if (generation === this.generation && setCurrentSpace) {
          this.membersError = error instanceof Error ? error.message : '成员加载失败'
        }
        throw error
      }
      const transfers = await fetchOwnershipTransfers(spaceId).catch(() => [])
      if (generation !== this.generation || (setCurrentSpace && this.currentSpaceId !== spaceId)) return []
      this.transfers = transfers
      const refs = await fetchSpaceProfileRefs(spaceId).catch(() => [])
      if (generation !== this.generation || (setCurrentSpace && this.currentSpaceId !== spaceId)) return []
      this.profileRefs = refs
      return loadedMembers
    },
    async create(name: string, kind: 'household' | 'lineage' = 'household') {
      const space = await createSpace(name, kind)
      this.spaces.unshift(space)
      await this.loadMembers(space.id)
      return space
    },
    /**
     * 空间基本设置（PATCH /spaces/{space_id}）：仅空间名等既有字段。
     * 成功后用服务端响应替换列表中的同一空间（无乐观本地改名）；
     * 授权由服务端 _require_space_manager 判定，前端不预判。
     */
    async rename(spaceId: number, name: string) {
      const updated = await updateSpace(spaceId, name)
      const index = this.spaces.findIndex((s) => s.id === updated.id)
      if (index !== -1) this.spaces.splice(index, 1, updated)
      return updated
    },
    /**
     * 设置/解除家庭空间所属家族（PUT /spaces/{id}/lineage-link）。
     * 授权由服务端判定（该空间管理员 + 目标 lineage active 成员）；
     * 成功后用服务端响应替换列表中的同一空间（无乐观本地更新）。
     */
    async setLineageLink(spaceId: number, lineageSpaceId: number | null) {
      const updated = await setSpaceLineageLink(spaceId, lineageSpaceId)
      const index = this.spaces.findIndex((s) => s.id === updated.id)
      if (index !== -1) this.spaces.splice(index, 1, updated)
      return updated
    },
    async invite(userId: number) {
      if (!this.canInvite) throw new Error('SPACE_FORBIDDEN_ACTOR')
      const space = this.currentSpace
      if (!space) throw new Error('NO_CURRENT_SPACE')
      await inviteToSpace(space.id, userId)
      await this.loadMembers(space.id)
    },
    async resolve(memberId: number, action: 'accept' | 'reject') {
      await resolveMembership(memberId, action)
      await this.load()
    },
    /**
     * 家庭空间成员申请读取该家庭所属的家族空间（独立申请，由目标本人审批）。
     *
     * 只登记 pending；批准前家族树仍返回安全 404。pending 不改变我的空间列表，
     * 故不触发列表重读。
     */
    async requestLineageAccess(householdSpaceId: number) {
      return requestLineageAccess(householdSpaceId)
    },
    async leaveOrRemove(memberId: number) {
      await removeOrWithdrawMembership(memberId)
      await this.load()
    },
    /** 发起 owner 移交（仅 owner；后端校验目标为活跃成员，FSM 同空间至多一个 pending） */
    async initiateTransfer(toUserId: number) {
      const space = this.currentSpace
      if (!space) throw new Error('NO_CURRENT_SPACE')
      await createOwnershipTransfer(space.id, toUserId)
      this.transfers = await fetchOwnershipTransfers(space.id).catch(() => [])
    },
    /** 受让人接受 / 任一方取消 pending 移交（commands.ownership FSM） */
    async respondTransfer(transferId: number, action: 'accept' | 'cancel') {
      await respondOwnershipTransfer(transferId, action)
      const space = this.currentSpace
      if (space) {
        this.transfers = await fetchOwnershipTransfers(space.id).catch(() => [])
        await this.loadMembers(space.id)
      }
    },
    clear() {
      this.generation += 1
      this.loading = false
      this.spaces = []
      this.members = []
      this.profileRefs = []
      this.transfers = []
      this.membersError = null
      this.currentSpaceId = null
    },
  },
})
