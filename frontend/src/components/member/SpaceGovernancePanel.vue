<script setup lang="ts">
import { computed, h, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NInput,
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
 * 可复用的空间治理内容（v2 §0.2/§0.5）。
 *
 * 此组件只消费空间 store 的 active membership 派生权限，不把平台运营者、
 * 档案 custody 或确档状态当作空间权限。它既可放在弹窗，也可作为独立空间
 * 管理看板的主体；所有写操作仍经 spaces store → space-scoped domain API。
 */
const auth = useAuthStore()
const spaces = useSpacesStore()
const message = useMessage()

// 对外只显示一个「空间管理员」；不再区分所有者与管理员两个等级。
const ROLE_LABELS: Record<SpaceRole, string> = {
  space_admin: '空间管理员',
  member: '成员',
  guest: '访客',
}
const ROLE_BADGE_CLASS: Record<SpaceRole, string> = {
  space_admin: 'fg-badge fg-badge--accent',
  member: 'fg-badge fg-badge--neutral',
  guest: 'fg-badge fg-badge--provisional',
}

const keyword = ref('')
const candidates = ref<Member[]>([])
const invitingId = ref<number | null>(null)
const transferTargetId = ref<number | null>(null)
const transferring = ref(false)

const myUserId = computed(() => auth.user?.id ?? -1)
const myRole = computed<SpaceRole | null>(() => spaces.currentRole)
const pendingTransfer = computed(() => spaces.pendingTransfers[0] ?? null)
const pendingTransferForMe = computed(() => pendingTransfer.value?.to_user === myUserId.value)
const pendingTransferByMe = computed(() => pendingTransfer.value?.from_user === myUserId.value)
const transferCandidates = computed(() =>
  spaces.activeMembers.filter((m) => m.user_id !== myUserId.value),
)
const transferOptions = computed<SelectOption[]>(() =>
  transferCandidates.value.map((m) => ({ label: memberName(m), value: m.user_id })),
)
const pendingCount = computed(() => spaces.members.filter((m) => m.status === 'pending').length)

function memberName(member: SpaceMemberInfo): string {
  return member.user_name ?? `#${member.user_id}`
}

const memberColumns = computed<DataTableColumns<SpaceMemberInfo>>(() => [
  { title: '名字', key: 'name', render: (row) => memberName(row) },
  {
    title: '角色',
    key: 'role',
    width: 120,
    render: (row) => h('span', { class: ROLE_BADGE_CLASS[row.role] }, ROLE_LABELS[row.role]),
  },
  {
    title: '状态',
    key: 'status',
    width: 84,
    render: (row) =>
      h(
        'span',
        {
          class:
            row.status === 'active'
              ? 'fg-badge fg-badge--confirmed'
              : 'fg-badge fg-badge--proposed',
        },
        row.status === 'active' ? '已加入' : '待确认',
      ),
  },
])

async function searchCandidates(): Promise<void> {
  if (!keyword.value.trim() || !spaces.canInvite) return
  try {
    const data = await fetchMembersByPrefix(keyword.value.trim())
    const memberIds = new Set(spaces.members.map((m) => m.user_id))
    candidates.value = data.filter((m) => m.id !== myUserId.value && !memberIds.has(m.id))
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
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

async function initiateTransfer(): Promise<void> {
  if (transferTargetId.value === null || !spaces.canTransferOwnership) return
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
    message.success(action === 'accept' ? '你已成为该空间管理员' : '移交已取消')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败')
  }
}
</script>

<template>
  <div class="governance-panel" data-test="space-governance-panel">
    <div class="summary-grid" data-test="space-management-summary">
      <div class="summary-item">
        <span class="summary-label">成员数</span>
        <strong data-test="space-member-count">{{ spaces.currentSpace?.member_count ?? spaces.activeMembers.length }}</strong>
      </div>
      <div class="summary-item">
        <span class="summary-label">待处理数</span>
        <strong data-test="space-pending-count">{{ pendingCount }}</strong>
      </div>
    </div>

    <div class="badges" data-test="space-badges">
      <span
        class="fg-badge"
        :class="spaces.currentSpace?.kind === 'lineage' ? 'fg-badge--accent' : 'fg-badge--confirmed'"
      >
        {{ spaces.currentSpace?.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
      </span>
      <span v-if="myRole" class="fg-badge" :class="ROLE_BADGE_CLASS[myRole]" data-test="my-role-tag">
        当前角色：{{ ROLE_LABELS[myRole] }}
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

    <h3 class="block-title">成员</h3>
    <NDataTable
      size="small"
      :columns="memberColumns"
      :data="spaces.members"
      :row-key="(row: SpaceMemberInfo) => row.id"
      data-test="member-table"
    />

    <template v-if="spaces.canInvite">
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

    <h3 class="block-title">空间管理员交接</h3>
    <div v-if="pendingTransfer" class="transfer-pending" data-test="transfer-pending">
      <template v-if="pendingTransferForMe">
        <span>「{{ spaces.currentSpace?.name }}」的管理员请求把空间管理权交接给你。</span>
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
    <template v-else-if="spaces.canTransferOwnership">
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
      <p class="hint">交接后对方成为该空间唯一管理员，你降为普通成员；删除档案前必须先完成交接。</p>
    </template>
  </div>
</template>

<style scoped>
.governance-panel { display: flex; flex-direction: column; min-width: 0; }
.summary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.summary-item { display: flex; flex-direction: column; gap: 3px; padding: 12px; background: var(--fg-surface-sunken); border: 1px solid var(--fg-line); border-radius: var(--fg-radius-control); }
.summary-label { color: var(--fg-ink-secondary); font-size: 12px; }
.summary-item strong { color: var(--fg-ink); font-size: 20px; }
.badges { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.guest-hint { margin-bottom: 12px; }
.block-title { margin: 16px 0 8px; font-size: 14px; color: var(--fg-ink); }
.invite-row, .transfer-row { display: flex; gap: 8px; }
.invite-input, .transfer-select { flex: 1; }
.candidates, .ref-list { list-style: none; margin: 8px 0 0; padding: 0; }
.candidate-row, .ref-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 0; color: var(--fg-ink); }
.transfer-pending { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; background: var(--fg-surface-sunken); border-radius: var(--fg-radius-control); font-size: 13px; color: var(--fg-ink); }
.transfer-actions { display: flex; gap: 8px; }
.hint { margin: 8px 0 0; color: var(--fg-ink-secondary); font-size: 12px; line-height: 1.5; }
@media (max-width: 560px) { .invite-row, .transfer-row { flex-direction: column; } .summary-grid { grid-template-columns: 1fr 1fr; } }
</style>
