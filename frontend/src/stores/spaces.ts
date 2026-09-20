import { defineStore } from 'pinia'

import { householdForLineage, lineageForSpace } from '@/composables/spaceSelection'

import { useAuthStore } from '@/stores/auth'
import type {
  FamilySpace,
  OwnershipTransfer,
  PendingInvitation,
  SpaceMemberInfo,
  SpaceProfileRefInfo,
} from '@/types/api'

import {
  createOwnershipTransfer,
  createSpace,
  fetchMyInvitations,
  fetchOwnershipTransfers,
  fetchSpaceMembers,
  fetchSpaceProfileRefs,
  fetchSpaces,
  approveMembership,
  inviteIntoFamilyHousehold,
  inviteToSpace as inviteToSpaceRequest,
  joinByUser as joinByUserRequest,
  removeOrWithdrawMembership,
  requestLineageAccess,
  setMemberRelationLabel,
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
    /**
     * 发给我的 / 我发起的 pending 空间邀请（跨全部空间，自足投影）。
     *
     * 与 `members` 分离：members 只描述**当前**空间，而邀请必须在我当前空间不是
     * 邀请空间时依然可达（这正是邀请此前不可见的根因）。
     */
    invitations: [] as PendingInvitation[],
    invitationsError: null as string | null,
    membersError: null as string | null,
    loading: false,
  }),
  getters: {
    /**
     * 当前空间：严格按 currentSpaceId 解析。
     *
     * 不做「列表第一个」兜底：服务端按 created_at 排序，用列表首项当默认空间
     * 正是启动期显示错误空间的原因（09-20 走查实测）。无上下文时返回 null，
     * 由启动决策（useSpaceContext.ensureDefaultSpace）决定落点。
     */
    currentSpace(state): FamilySpace | null {
      if (state.currentSpaceId === null) return null
      return state.spaces.find((s) => s.id === state.currentSpaceId) ?? null
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
     * household → 所属 lineage：判定规则在 spaceSelection（与启动决策共用同一份，
     * 不在此复制第二份规则）。
     */
    lineageForSpace(state) {
      return (spaceId: number): FamilySpace | null => lineageForSpace(state.spaces, spaceId)
    },
    /**
     * lineage → 落点 household（家庭卡页的家族切换目标）：规则同上，共用纯函数。
     */
    householdForLineage(state) {
      return (lineageId: number): FamilySpace | null =>
        householdForLineage(state.spaces, lineageId, {
          currentSpaceId: state.currentSpaceId,
          userId: useAuthStore().user?.id ?? null,
        })
    },
    activeMembers(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'active')
    },
    pendingForMe(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'pending')
    },
    /**
     * 等我接受、且当前真的可以接受的邀请条数（账号菜单角标）。
     *
     * 只算 `direction='incoming'` + `stage='awaiting_me'`：等我批准的那类不是我的待办，
     * 而等房主批准的 incoming 我此时也不能接受（服务端 403）。
     */
    invitationsAwaitingMe(state): number {
      return state.invitations.filter(
        (item) => item.direction === 'incoming' && item.stage === 'awaiting_me',
      ).length
    },
    /** 待我接受（可操作）与我发起的申请（只读进度）两段，与视图分区一一对应 */
    incomingInvitations(state): PendingInvitation[] {
      return state.invitations.filter((item) => item.direction === 'incoming')
    },
    outgoingInvitations(state): PendingInvitation[] {
      return state.invitations.filter((item) => item.direction === 'outgoing')
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
        // 本 action 只刷新列表投影，**不挑选默认空间**：服务端按 created_at 排序，
        // 「最新加入的空间」不等于用户想先看到的空间。默认空间由
        // useSpaceContext.ensureDefaultSpace（启动期唯一决策点，含按路由落点）
        // 决定；这里保留既有 currentSpaceId，避免启动期出现第二个写入者。
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
    async invite(userId: number, relationLabel: string) {
      if (!this.canInvite) throw new Error('SPACE_FORBIDDEN_ACTOR')
      const space = this.currentSpace
      if (!space) throw new Error('NO_CURRENT_SPACE')
      await inviteToSpaceRequest(space.id, userId, relationLabel)
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
    /**
     * 房主批准一条待处理加入（09-20 审批链）。
     *
     * 申请人/发起人不得自批；批准后按 origin 决定是仍需受邀人接受还是直接生效。
     */
    async approveMember(memberId: number) {
      const updated = await approveMembership(memberId)
      const spaceId = this.currentSpace?.id
      if (spaceId !== undefined) await this.loadMembers(spaceId)
      return updated
    },
    /**
     * 设置/清除我与某成员之间的关系词（仅两端本人；即时生效）。
     */
    async setRelationLabel(spaceId: number, otherUserId: number, label: string) {
      return setMemberRelationLabel(spaceId, otherUserId, label)
    },
    /**
     * 邀请指定成员加入**指定**家庭空间（空间治理面板入口）。
     *
     * 不复用只作用于 currentSpace 的 `invite`：调用方可能显式指定空间；授权仍由
     * 服务端按所选空间复核。只产生 pending。
     */
    async inviteToSpace(spaceId: number, userId: number, relationLabel: string) {
      return inviteToSpaceRequest(spaceId, userId, relationLabel)
    },
    /**
     * 在当前家族空间范围内邀请对方加入我的家庭空间（个人公示页入口）。
     *
     * 服务端复核双方同族与该空间归属；只产生 pending。
     */
    async inviteIntoFamilyHousehold(
      lineageSpaceId: number,
      spaceId: number,
      userId: number,
      relationLabel: string,
    ) {
      return inviteIntoFamilyHousehold(lineageSpaceId, spaceId, userId, relationLabel)
    },
    /**
     * 申请加入对方在当前家族空间下的家庭空间（个人公示页入口）。
     *
     * 只产生 pending，由该家庭空间管理员批准。
     */
    async requestJoinInFamily(
      lineageSpaceId: number,
      targetUserId: number,
      relationLabel: string,
      spaceId?: number,
    ) {
      return joinByUserRequest(lineageSpaceId, targetUserId, relationLabel, spaceId)
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
    /**
     * 读取发给我的 / 我发起的 pending 邀请（跨全部空间）。
     *
     * 世代校验同 `load`：登出或切会话后的迟到响应不得回写新状态。
     */
    async loadInvitations(): Promise<PendingInvitation[]> {
      const generation = this.generation
      this.invitationsError = null
      try {
        const rows = await fetchMyInvitations()
        if (generation !== this.generation) return []
        this.invitations = rows
        return rows
      } catch (error) {
        if (generation === this.generation) {
          this.invitationsError = error instanceof Error ? error.message : '邀请加载失败'
        }
        throw error
      }
    },
    /**
     * 接受 / 拒绝一条发给我的邀请（复用既有成员决议端点）。
     *
     * 授权与顺序仍由服务端判定（未获房主批准时接受返回 403）；成功后重读邀请列表，
     * 接受时再 `load()` 让新空间进入「我的空间」。
     */
    async resolveInvitation(memberId: number, action: 'accept' | 'reject') {
      const updated = await resolveMembership(memberId, action)
      await this.loadInvitations()
      if (action === 'accept') await this.load()
      return updated
    },
    /** 撤回我发起的 pending（既有 D8 断连轨：pending 时发起方可撤回）。 */
    async withdrawInvitation(memberId: number) {
      await removeOrWithdrawMembership(memberId)
      await this.loadInvitations()
    },
    clear() {
      this.generation += 1
      this.loading = false
      this.spaces = []
      this.members = []
      this.profileRefs = []
      this.transfers = []
      this.invitations = []
      this.invitationsError = null
      this.membersError = null
      this.currentSpaceId = null
    },
  },
})
