<script setup lang="ts">
import { computed, h, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NInput,
  NModal,
  NSelect,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchMembersByPrefix } from '@/api/members'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { Member, SpaceMemberInfo, SpaceRole } from '@/types/api'

/**
 * 空间治理弹窗（v2 §0.2/§0.5）：成员与四角色展示、owner/space_admin 邀请、
 * owner 移交发起/接受/取消。provisional 引用（space_profile_refs）后端暂无
 * 读取端点，故仅列正式 SpaceMember。
 */
defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const auth = useAuthStore()
const spaces = useSpacesStore()
const message = useMessage()

const ROLE_LABELS: Record<SpaceRole, string> = {
  owner: '所有者',
  space_admin: '管理员',
  member: '成员',
  guest: '访客',
}
/** 角色 → 领域徽章（--fg-status-* 同源）：owner=主色实底 / admin=proposed /
 * member=中性 / guest=provisional 虚线章（.fg-badge--* 见 tokens.css） */
const ROLE_BADGE_CLASS: Record<SpaceRole, string> = {
  owner: 'fg-badge fg-badge--accent',
  space_admin: 'fg-badge fg-badge--proposed',
  member: 'fg-badge fg-badge--neutral',
  guest: 'fg-badge fg-badge--provisional',
}

// ---- 邀请 ----
const keyword = ref('')
const candidates = ref<Member[]>([])
const invitingId = ref<number | null>(null)

// ---- 移交 ----
const transferTargetId = ref<number | null>(null)
const transferring = ref(false)

const myUserId = computed(() => auth.user?.id ?? -1)
const myRole = computed<SpaceRole | null>(() => {
  const mine = spaces.members.find((m) => m.user_id === myUserId.value && m.status === 'active')
  return mine?.role ?? null
})
const canInvite = computed(() => myRole.value === 'owner' || myRole.value === 'space_admin')
const isOwner = computed(() => myRole.value === 'owner')

/** 当前空间的 pending 移交（FSM 保证至多一个） */
const pendingTransfer = computed(
  () => spaces.transfers.find((t) => t.status === 'pending') ?? null,
)
const pendingTransferForMe = computed(
  () => pendingTransfer.value?.to_user === myUserId.value,
)
const pendingTransferByMe = computed(
  () => pendingTransfer.value?.from_user === myUserId.value,
)

/** 可移交对象：本空间 active 成员且非自己（后端 FSM 兜底校验） */
const transferCandidates = computed(() =>
  spaces.activeMembers.filter((m) => m.user_id !== myUserId.value),
)

const transferOptions = computed<SelectOption[]>(() =>
  transferCandidates.value.map((m) => ({ label: memberName(m), value: m.user_id })),
)

function memberName(m: SpaceMemberInfo): string {
  return m.user_name ?? `#${m.user_id}`
}

const memberColumns = computed<DataTableColumns<SpaceMemberInfo>>(() => [
  {
    title: '名字',
    key: 'name',
    render: (row) => memberName(row),
  },
  {
    title: '角色',
    key: 'role',
    width: 100,
    render: (row) =>
      h('span', { class: ROLE_BADGE_CLASS[row.role] }, ROLE_LABELS[row.role]),
  },
  {
    title: '状态',
    key: 'status',
    width: 84,
    render: (row) =>
      h(
        'span',
        { class: row.status === 'active' ? 'fg-badge fg-badge--confirmed' : 'fg-badge fg-badge--proposed' },
        row.status === 'active' ? '已加入' : '待确认',
      ),
  },
])

async function searchCandidates(): Promise<void> {
  if (!keyword.value.trim()) return
  try {
    const data = await fetchMembersByPrefix(keyword.value.trim())
    const memberIds = new Set(spaces.members.map((m) => m.user_id))
    candidates.value = data.filter((m) => m.id !== myUserId.value && !memberIds.has(m.id))
  } catch {
    message.error('搜索失败，请稍后重试')
  }
}

async function invite(member: Member): Promise<void> {
  invitingId.value = member.id
  try {
    await spaces.invite(member.id)
    message.success('邀请已发送')
    candidates.value = candidates.value.filter((m) => m.id !== member.id)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

async function initiateTransfer(): Promise<void> {
  if (transferTargetId.value === null) return
  transferring.value = true
  try {
    await spaces.initiateTransfer(transferTargetId.value)
    message.success('移交请求已发出，等待对方接受')
    transferTargetId.value = null
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '发起移交失败')
  } finally {
    transferring.value = false
  }
}

async function respondTransfer(action: 'accept' | 'cancel'): Promise<void> {
  const transfer = pendingTransfer.value
  if (!transfer) return
  try {
    await spaces.respondTransfer(transfer.id, action)
    message.success(action === 'accept' ? '你已成为该空间所有者' : '移交已取消')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败')
  }
}

function close(): void {
  emit('update:visible', false)
}
</script>

<template>
  <NModal
    :show="visible"
    preset="card"
    :title="`空间管理 · ${spaces.currentSpace?.name ?? ''}`"
    data-test="space-governance-dialog"
    @update:show="emit('update:visible', $event)"
    @after-leave="close"
  >
    <!-- kind 与我的角色徽标 -->
    <div class="badges" data-test="space-badges">
      <span
        class="fg-badge"
        :class="spaces.currentSpace?.kind === 'lineage' ? 'fg-badge--accent' : 'fg-badge--confirmed'"
      >
        {{ spaces.currentSpace?.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
      </span>
      <span v-if="myRole" class="fg-badge" :class="ROLE_BADGE_CLASS[myRole]" data-test="my-role-tag">
        我的角色：{{ ROLE_LABELS[myRole] }}
      </span>
    </div>

    <NAlert
      v-if="myRole === 'guest'"
      type="info"
      :show-icon="true"
      class="guest-hint"
      data-test="guest-hint"
    >
      你以访客身份参与此空间，仅可见最小化信息，不获得家庭详情。
    </NAlert>

    <!-- 成员列表 -->
    <h3 class="block-title">成员</h3>
    <NDataTable
      size="small"
      :columns="memberColumns"
      :data="spaces.members"
      :row-key="(row: SpaceMemberInfo) => row.id"
      data-test="member-table"
    />

    <!-- 邀请（owner / space_admin） -->
    <template v-if="canInvite">
      <h3 class="block-title">邀请新成员</h3>
      <div class="invite-row">
        <NInput
          v-model:value="keyword"
          placeholder="输入名字前缀搜索已有账号"
          class="invite-input"
          data-test="governance-invite-search"
          @keyup.enter="searchCandidates"
        />
        <NButton data-test="governance-invite-search-btn" @click="searchCandidates">搜索</NButton>
      </div>
      <ul class="candidates">
        <li v-for="m in candidates" :key="m.id" class="candidate-row">
          <span>{{ m.name }}（#{{ m.id }}）</span>
          <NButton
            size="tiny"
            type="primary"
            secondary
            :loading="invitingId === m.id"
            :data-test="`governance-invite-${m.id}`"
            @click="invite(m)"
          >
            邀请
          </NButton>
        </li>
      </ul>
    </template>

    <!-- owner 移交（AC-F5） -->
    <h3 class="block-title">空间所有权移交</h3>
    <div v-if="pendingTransfer" class="transfer-pending" data-test="transfer-pending">
      <template v-if="pendingTransferForMe">
        <span>
          「{{ spaces.spaces.find((s) => s.id === pendingTransfer?.space_id)?.name }}」的所有者请求把空间移交给你。
        </span>
        <div class="transfer-actions">
          <NButton type="primary" size="small" data-test="transfer-accept" @click="respondTransfer('accept')">
            接受
          </NButton>
          <NButton size="small" data-test="transfer-decline" @click="respondTransfer('cancel')">
            谢绝
          </NButton>
        </div>
      </template>
      <template v-else-if="pendingTransferByMe">
        <span>等待对方接受你的移交请求…</span>
        <NButton size="small" data-test="transfer-cancel" @click="respondTransfer('cancel')">
          取消移交
        </NButton>
      </template>
      <template v-else>
        <span>本空间有一份待处理的移交请求。</span>
      </template>
    </div>
    <template v-else-if="isOwner">
      <div class="transfer-row">
        <NSelect
          v-model:value="transferTargetId"
          placeholder="选择接任者（活跃成员）"
          class="transfer-select"
          :options="transferOptions"
          data-test="transfer-target-select"
        />
        <NButton
          type="warning"
          secondary
          :loading="transferring"
          :disabled="transferTargetId === null"
          data-test="transfer-initiate"
          @click="initiateTransfer"
        >
          发起移交
        </NButton>
      </div>
      <p class="hint">移交后你将降为管理员；删除档案前必须完成移交或退出所有权。</p>
    </template>

    <template #footer>
      <div class="footer-actions">
        <NButton data-test="governance-close" @click="close">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.badges {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.guest-hint {
  margin-bottom: 12px;
}

.block-title {
  margin: 16px 0 8px;
  font-size: 14px;
  color: var(--fg-ink);
}

.invite-row {
  display: flex;
  gap: 8px;
}

.invite-input {
  flex: 1;
}

.candidates {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}

.candidate-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  color: var(--fg-ink);
}

.transfer-row {
  display: flex;
  gap: 8px;
}

.transfer-select {
  flex: 1;
}

.transfer-pending {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background-color: var(--fg-surface-sunken);
  border-radius: var(--fg-radius-control);
  font-size: 13px;
  color: var(--fg-ink);
}

.transfer-actions {
  display: flex;
  gap: 8px;
}

.hint {
  margin-top: 8px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='space-governance-dialog'] {
  width: min(560px, calc(100vw - 48px));
}
</style>
