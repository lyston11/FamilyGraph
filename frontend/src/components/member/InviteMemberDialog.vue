<script setup lang="ts">
import { ref } from 'vue'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchMembersByPrefix } from '@/api/members'
import { useSpacesStore } from '@/stores/spaces'
import type { Member } from '@/types/api'

/**
 * 邀请成员弹窗（自旧 HomeView 内联逻辑抽出，09-01 Phase 3 流程保全）。
 *
 * - 走既有邀请流程：按名字前缀搜索已有账号 → `POST /spaces/{id}/members`
 *   （spaces store.invite），受邀人本人接受后才 active，绝不静默拉人入空间；
 * - 授权（active member 除 guest）由 spaces store 的 canInvite 判定，本组件
 *   不做本地角色推断；调用方也应在打开入口前用同一 getter 控制可见性；
 * - 打开时聚焦搜索，关闭即清空候选与关键字（不留内存态）。
 */
defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'invited', member: Member): void
}>()

const spaces = useSpacesStore()
const message = useMessage()

const keyword = ref('')
const candidates = ref<Member[]>([])
const invitingId = ref<number | null>(null)

const searchInputProps = {
  'data-test': 'invite-search',
  'aria-label': '按名字前缀搜索成员',
} as VueInputHTMLAttributes

async function search(): Promise<void> {
  if (!spaces.canInvite || !keyword.value.trim()) return
  try {
    const data = await fetchMembersByPrefix(keyword.value.trim())
    // 已在本空间的账号不再出现（以服务端成员关系为准）
    const currentIds = new Set(spaces.members.map((m) => m.user_id))
    candidates.value = data.filter((m) => !currentIds.has(m.id))
  } catch {
    message.error('搜索失败，请稍后重试')
  }
}

async function invite(member: Member): Promise<void> {
  if (!spaces.canInvite) return
  invitingId.value = member.id
  try {
    await spaces.invite(member.id)
    message.success('邀请已发送')
    candidates.value = candidates.value.filter((m) => m.id !== member.id)
    emit('invited', member)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

function close(): void {
  emit('update:visible', false)
  keyword.value = ''
  candidates.value = []
  invitingId.value = null
}
</script>

<template>
  <NModal
    :show="visible"
    preset="card"
    title="邀请成员"
    data-test="invite-dialog"
    @update:show="$event ? undefined : close()"
  >
    <NInput
      v-model:value="keyword"
      placeholder="输入名字前缀搜索"
      :input-props="searchInputProps"
      @keyup.enter="search"
    />
    <ul class="candidates" data-test="invite-candidates">
      <li v-for="m in candidates" :key="m.id" class="candidate-row">
        <span>{{ m.name }}（#{{ m.id }}）</span>
        <NButton
          size="tiny"
          type="primary"
          secondary
          :loading="invitingId === m.id"
          :data-test="`invite-user-${m.id}`"
          @click="invite(m)"
        >
          邀请
        </NButton>
      </li>
    </ul>
    <template #footer>
      <div class="modal-actions">
        <NButton data-test="invite-close" @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.candidates {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}

.candidate-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 36px;
  padding: 6px 0;
  color: var(--fg-ink);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='invite-dialog'] {
  width: min(380px, calc(100vw - 48px));
}
</style>
