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
  resolveMembership,
  respondOwnershipTransfer,
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
      return this.currentMembership?.role ?? null
    },
    isSpaceOwner(): boolean {
      return this.currentRole === 'owner'
    },
    isSpaceAdmin(): boolean {
      return this.currentRole === 'space_admin'
    },
    canManageSpace(): boolean {
      return this.isSpaceOwner || this.isSpaceAdmin
    },
    /** 邀请授权：当前空间 active 成员（除 guest）均可邀请；受邀人仍需本人接受。 */
    canInvite(): boolean {
      return this.currentMembership !== null && this.currentRole !== 'guest'
    },
    canTransferOwnership(): boolean {
      return this.isSpaceOwner
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
    async loadMembers(spaceId: number) {
      const generation = this.generation
      this.currentSpaceId = spaceId
      // 切换或重新校验前先丢弃旧空间授权缓存；请求失败不得沿用旧 membership。
      this.members = []
      this.transfers = []
      this.profileRefs = []
      const members = await fetchSpaceMembers(spaceId)
      if (generation !== this.generation || this.currentSpaceId !== spaceId) return
      this.members = members
      const transfers = await fetchOwnershipTransfers(spaceId).catch(() => [])
      if (generation !== this.generation || this.currentSpaceId !== spaceId) return
      this.transfers = transfers
      const refs = await fetchSpaceProfileRefs(spaceId).catch(() => [])
      if (generation !== this.generation || this.currentSpaceId !== spaceId) return
      this.profileRefs = refs
    },
    async create(name: string, kind: 'household' | 'lineage' = 'household') {
      const space = await createSpace(name, kind)
      this.spaces.unshift(space)
      await this.loadMembers(space.id)
      return space
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
      this.currentSpaceId = null
    },
  },
})
