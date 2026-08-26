import { defineStore } from 'pinia'

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

/** 家庭空间状态（m1c）。AD-3：空列表时由首页引导创建默认空间。 */
export const useSpacesStore = defineStore('spaces', {
  state: () => ({
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
    activeMembers(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'active')
    },
    pendingForMe(state): SpaceMemberInfo[] {
      return state.members.filter((m) => m.status === 'pending')
    },
    /** 发给我的 pending 移交（受让人视角；userId 由组件/store 动作传入比对） */
    pendingTransfers(state): OwnershipTransfer[] {
      return state.transfers.filter((t) => t.status === 'pending')
    },
  },
  actions: {
    async load() {
      this.loading = true
      try {
        this.spaces = await fetchSpaces()
        if (this.currentSpaceId === null && this.spaces.length > 0) {
          this.currentSpaceId = this.spaces[0].id
        }
        if (this.currentSpaceId !== null) await this.loadMembers(this.currentSpaceId)
        else this.members = []
      } finally {
        this.loading = false
      }
    },
    async loadMembers(spaceId: number) {
      this.currentSpaceId = spaceId
      this.members = await fetchSpaceMembers(spaceId)
      this.transfers = await fetchOwnershipTransfers(spaceId).catch(() => [])
      this.profileRefs = await fetchSpaceProfileRefs(spaceId).catch(() => [])
    },
    async create(name: string) {
      const space = await createSpace(name)
      this.spaces.unshift(space)
      await this.loadMembers(space.id)
      return space
    },
    async invite(userId: number) {
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
      this.spaces = []
      this.members = []
      this.profileRefs = []
      this.transfers = []
      this.currentSpaceId = null
    },
  },
})
