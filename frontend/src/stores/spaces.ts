import { defineStore } from 'pinia'

import type { FamilySpace, SpaceMemberInfo } from '@/types/api'

import {
  createSpace,
  fetchSpaceMembers,
  fetchSpaces,
  inviteToSpace,
  removeOrWithdrawMembership,
  resolveMembership,
} from '@/api/spaces'

/** 家庭空间状态（m1c）。AD-3：空列表时由首页引导创建默认空间。 */
export const useSpacesStore = defineStore('spaces', {
  state: () => ({
    spaces: [] as FamilySpace[],
    /** 当前查看的空间（默认优先级见 architecture §3） */
    currentSpaceId: null as number | null,
    members: [] as SpaceMemberInfo[],
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
    clear() {
      this.spaces = []
      this.members = []
      this.currentSpaceId = null
    },
  },
})
