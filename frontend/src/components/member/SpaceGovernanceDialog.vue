<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

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

const ROLE_LABELS: Record<SpaceRole, string> = {
  owner: '所有者',
  space_admin: '管理员',
  member: '成员',
  guest: '访客',
}
const ROLE_TAG_TYPES: Record<SpaceRole, 'danger' | 'warning' | 'info' | 'success'> = {
  owner: 'danger',
  space_admin: 'warning',
  member: 'info',
  guest: 'success',
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

function memberName(m: SpaceMemberInfo): string {
  return m.user_name ?? `#${m.user_id}`
}

async function searchCandidates(): Promise<void> {
  if (!keyword.value.trim()) return
  try {
    const data = await fetchMembersByPrefix(keyword.value.trim())
    const memberIds = new Set(spaces.members.map((m) => m.user_id))
    candidates.value = data.filter((m) => m.id !== myUserId.value && !memberIds.has(m.id))
  } catch {
    ElMessage.error('搜索失败，请稍后重试')
  }
}

async function invite(member: Member): Promise<void> {
  invitingId.value = member.id
  try {
    await spaces.invite(member.id)
    ElMessage.success('邀请已发送')
    candidates.value = candidates.value.filter((m) => m.id !== member.id)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

async function initiateTransfer(): Promise<void> {
  if (transferTargetId.value === null) return
  transferring.value = true
  try {
    await spaces.initiateTransfer(transferTargetId.value)
    ElMessage.success('移交请求已发出，等待对方接受')
    transferTargetId.value = null
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '发起移交失败')
  } finally {
    transferring.value = false
  }
}

async function respondTransfer(action: 'accept' | 'cancel'): Promise<void> {
  const transfer = pendingTransfer.value
  if (!transfer) return
  try {
    await spaces.respondTransfer(transfer.id, action)
    ElMessage.success(action === 'accept' ? '你已成为该空间所有者' : '移交已取消')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败')
  }
}

function close(): void {
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="`空间管理 · ${spaces.currentSpace?.name ?? ''}`"
    width="560px"
    :data-test="'space-governance-dialog'"
    @update:model-value="emit('update:visible', $event)"
    @closed="close"
  >
    <!-- kind 与我的角色徽标 -->
    <div class="badges" data-test="space-badges">
      <el-tag size="small" :type="spaces.currentSpace?.kind === 'lineage' ? 'success' : 'primary'">
        {{ spaces.currentSpace?.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
      </el-tag>
      <el-tag v-if="myRole" size="small" :type="ROLE_TAG_TYPES[myRole]" :data-test="'my-role-tag'">
        我的角色：{{ ROLE_LABELS[myRole] }}
      </el-tag>
    </div>

    <el-alert
      v-if="myRole === 'guest'"
      type="info"
      :closable="false"
      class="guest-hint"
      data-test="guest-hint"
    >
      你以访客身份参与此空间，仅可见最小化信息，不获得家庭详情。
    </el-alert>

    <!-- 成员列表 -->
    <h3 class="block-title">成员</h3>
    <el-table :data="spaces.members" size="small" data-test="member-table">
      <el-table-column label="名字">
        <template #default="{ row }">{{ memberName(row) }}</template>
      </el-table-column>
      <el-table-column label="角色" width="110">
        <template #default="{ row }">
          <el-tag size="small" :type="ROLE_TAG_TYPES[row.role as SpaceRole]">
            {{ ROLE_LABELS[row.role as SpaceRole] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'warning'">
            {{ row.status === 'active' ? '已加入' : '待确认' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <!-- 邀请（owner / space_admin） -->
    <template v-if="canInvite">
      <h3 class="block-title">邀请新成员</h3>
      <div class="invite-row">
        <el-input
          v-model="keyword"
          placeholder="输入名字前缀搜索已有账号"
          class="invite-input"
          data-test="governance-invite-search"
          @keyup.enter="searchCandidates"
        />
        <el-button data-test="governance-invite-search-btn" @click="searchCandidates">搜索</el-button>
      </div>
      <ul class="candidates">
        <li v-for="m in candidates" :key="m.id" class="candidate-row">
          <span>{{ m.name }}（#{{ m.id }}）</span>
          <el-button
            size="small"
            type="primary"
            :loading="invitingId === m.id"
            :data-test="`governance-invite-${m.id}`"
            @click="invite(m)"
          >
            邀请
          </el-button>
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
          <el-button type="primary" size="small" data-test="transfer-accept" @click="respondTransfer('accept')">
            接受
          </el-button>
          <el-button size="small" data-test="transfer-decline" @click="respondTransfer('cancel')">
            谢绝
          </el-button>
        </div>
      </template>
      <template v-else-if="pendingTransferByMe">
        <span>等待对方接受你的移交请求…</span>
        <el-button size="small" data-test="transfer-cancel" @click="respondTransfer('cancel')">
          取消移交
        </el-button>
      </template>
      <template v-else>
        <span>本空间有一份待处理的移交请求。</span>
      </template>
    </div>
    <template v-else-if="isOwner">
      <div class="transfer-row">
        <el-select
          v-model="transferTargetId"
          placeholder="选择接任者（活跃成员）"
          class="transfer-select"
          data-test="transfer-target-select"
        >
          <el-option
            v-for="m in transferCandidates"
            :key="m.id"
            :label="memberName(m)"
            :value="m.user_id"
          />
        </el-select>
        <el-button
          type="warning"
          plain
          :loading="transferring"
          :disabled="transferTargetId === null"
          data-test="transfer-initiate"
          @click="initiateTransfer"
        >
          发起移交
        </el-button>
      </div>
      <p class="hint">移交后你将降为管理员；删除档案前必须完成移交或退出所有权。</p>
    </template>

    <template #footer>
      <el-button data-test="governance-close" @click="close">关闭</el-button>
    </template>
  </el-dialog>
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
  padding: 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  font-size: 13px;
}

.transfer-actions {
  display: flex;
  gap: 8px;
}

.hint {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
