import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as membersApi from '@/api/members'
import type {
  ClanDisclosure,
  Member,
  MemberCreatePayload,
  MemberCreateResponse,
  MemberUpdatePayload,
} from '@/types/api'

/**
 * 成员档案状态（spec/frontend/state-management.md：服务端数据唯一来源是 store）。
 * 档案属敏感 PII：logout / token 失效时由 auth.clearSession 调用 clear() 清空。
 */
export const useMembersStore = defineStore('members', () => {
  const members = ref<Member[]>([])
  const loading = ref(false)
  /** 当前抽屉目标 id（null = 关闭） */
  const drawerTargetId = ref<number | null>(null)

  const drawerTarget = computed(
    () => members.value.find((member) => member.id === drawerTargetId.value) ?? null,
  )

  /** 会话代际：clear/登出后递增，旧请求的响应一律不得回写新会话状态（P2 隔离） */
  let storeGeneration = 0

  function clear(): void {
    storeGeneration += 1
    members.value = []
    drawerTargetId.value = null
    loading.value = false
  }

  async function load(): Promise<void> {
    const generation = storeGeneration
    loading.value = true
    try {
      const result = await membersApi.fetchMembers()
      if (generation === storeGeneration) members.value = result
    } finally {
      if (generation === storeGeneration) loading.value = false
    }
  }

  async function create(
    payload: MemberCreatePayload,
    idempotencyKey: string,
  ): Promise<MemberCreateResponse> {
    const result = await membersApi.createMember(payload, idempotencyKey)
    // 幂等重放不重复 push（首次已入列表）
    if (!members.value.some((m) => m.id === result.user.id)) {
      members.value.push(result.user)
    }
    return result
  }

  async function update(id: number, patch: MemberUpdatePayload): Promise<Member> {
    const updated = await membersApi.updateMember(id, patch)
    replaceOne(updated)
    return updated
  }

  async function setDisclosure(
    id: number,
    disclosure: ClanDisclosure,
    spaceId?: number,
  ): Promise<Member> {
    // 全局与逐空间共用端点；scopeId 仅在提供时透传，保持全局调用形状不变
    const updated =
      spaceId === undefined
        ? await membersApi.updateDisclosure(id, disclosure)
        : await membersApi.updateDisclosure(id, disclosure, spaceId)
    replaceOne(updated)
    return updated
  }

  async function remove(id: number, confirmName: string): Promise<void> {
    await membersApi.removeMember(id, confirmName)
    members.value = members.value.filter((member) => member.id !== id)
    if (drawerTargetId.value === id) {
      drawerTargetId.value = null
    }
  }

  function openDrawer(id: number): void {
    drawerTargetId.value = id
  }

  function closeDrawer(): void {
    drawerTargetId.value = null
  }

  function replaceOne(updated: Member): void {
    members.value = members.value.map((member) =>
      member.id === updated.id ? updated : member,
    )
  }

  return {
    members,
    loading,
    drawerTargetId,
    drawerTarget,
    clear,
    load,
    create,
    update,
    setDisclosure,
    remove,
    openDrawer,
    closeDrawer,
  }
})
