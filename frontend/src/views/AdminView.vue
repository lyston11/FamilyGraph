<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import {
  adminResetPin,
  createOwnerInvitation,
  fetchAdminClaimDisputes,
  fetchAdminDataRights,
  fetchAdminUsers,
  fetchAuditLogs,
  fetchOwnerInvitations,
  resolveClaimDispute,
  resolveCorrection,
  revokeOwnerInvitation,
  type AdminClaimDisputeRow,
  type AdminUserRow,
  type AuditRow,
} from '@/api/admin'
import { ApiError } from '@/api/errors'
import type { ClaimDispute, DataRightRequest, OwnerInvitation } from '@/types/api'

/**
 * 平台运营后台（v2 §0.2）：仅系统管理与 break-glass 数据兑底。
 * - 用户列表 / 重置 PIN / 审计时间线（原有）
 * - owner onboarding 邀请：签发（token 明文仅显示一次）/ 撤销
 * - 数据权利请求：更正决议（批准→按白名单字段应用；驳回）——理由必填 + 审计
 * - 认领争议决议：理由必填 + 审计，证据原文永不覆盖
 * operator 不提供任何家庭数据浏览权：本页不展示档案敏感字段。
 */

const users = ref<AdminUserRow[]>([])
const logs = ref<AuditRow[]>([])
const invitations = ref<OwnerInvitation[]>([])
const dataRights = ref<DataRightRequest[]>([])
const disputes = ref<AdminClaimDisputeRow[]>([])
const loading = ref(false)
const oneTimePin = ref('')
const oneTimeFor = ref('')

// ---- owner 邀请 ----
const issuedToken = ref('')
const issuedExpiresAt = ref('')
const issuing = ref(false)

// ---- 更正决议弹窗 ----
const correctionDialog = reactive({ visible: false, requestId: 0 })
const correctionApprove = ref(true)
const correctionNote = ref('')
const correctionSubmitting = ref(false)

// ---- 争议决议弹窗 ----
const disputeDialog = reactive({ visible: false, disputeId: 0 })
const disputeOutcome = ref<'resolved_claim' | 'resolved_reject'>('resolved_claim')
const disputeNote = ref('')
const disputeSubmitting = ref(false)

async function loadUsers() {
  users.value = await fetchAdminUsers()
}
async function loadLogs() {
  logs.value = await fetchAuditLogs()
}
async function loadInvitations() {
  invitations.value = await fetchOwnerInvitations()
}
async function loadDataRights() {
  dataRights.value = await fetchAdminDataRights()
}
async function loadDisputes() {
  disputes.value = await fetchAdminClaimDisputes()
}

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadUsers(), loadLogs(), loadInvitations(), loadDataRights(), loadDisputes()])
  } catch {
    ElMessage.error('加载失败（需要平台运营者身份）')
  } finally {
    loading.value = false
  }
})

async function resetPin(row: AdminUserRow) {
  try {
    await ElMessageBoxConfirmName(String(row.id), row.name)
    const { pin } = await adminResetPin(row.id)
    oneTimePin.value = pin
    oneTimeFor.value = row.name
    await loadLogs()
  } catch {
    /* 用户取消或失败 */
  }
}

async function ElMessageBoxConfirmName(_id: string, name: string): Promise<void> {
  // 简化确认：输入名字校验由后端 confirm 标志 + 前端弹窗承担
  const { ElMessageBox } = await import('element-plus')
  await ElMessageBox.prompt(`请输入「${name}」以确认重置 PIN`, '重置 PIN', {
    confirmButtonText: '确认重置',
    cancelButtonText: '取消',
    inputPattern: new RegExp(`^${name}$`),
    inputErrorMessage: '名字不一致',
  })
}

function invitationStatus(inv: OwnerInvitation): { text: string; type: 'success' | 'info' | 'danger' | 'warning' } {
  if (inv.used_at) return { text: '已兑换', type: 'info' }
  if (inv.revoked_at) return { text: '已撤销', type: 'danger' }
  if (new Date(inv.expires_at).getTime() < Date.now()) return { text: '已过期', type: 'warning' }
  return { text: '有效', type: 'success' }
}

async function issueInvitation(): Promise<void> {
  issuing.value = true
  try {
    const created = await createOwnerInvitation()
    issuedToken.value = created.token
    issuedExpiresAt.value = created.expires_at.replace('T', ' ').slice(0, 16)
    await loadInvitations()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '签发失败')
  } finally {
    issuing.value = false
  }
}

async function revokeInvitation(id: number): Promise<void> {
  try {
    await revokeOwnerInvitation(id)
    await loadInvitations()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '撤销失败')
  }
}

const DATA_RIGHT_TYPE_LABELS: Record<DataRightRequest['type'], string> = {
  export: '导出',
  correct: '更正',
  delete: '删除',
}
const DATA_RIGHT_STATUS_LABELS: Record<DataRightRequest['status'], string> = {
  pending: '待处理',
  processing: '处理中',
  completed: '已完成',
  rejected: '已驳回',
  expired: '已过期',
}

/** 可决议的更正申请（break-glass 入口） */
const resolvableCorrections = computed(() =>
  dataRights.value.filter((r) => r.type === 'correct' && r.status === 'pending'),
)

function openCorrectionDialog(requestId: number): void {
  correctionDialog.requestId = requestId
  correctionApprove.value = true
  correctionNote.value = ''
  correctionDialog.visible = true
}

async function submitCorrection(): Promise<void> {
  if (!correctionNote.value.trim()) return
  correctionSubmitting.value = true
  try {
    await resolveCorrection(correctionDialog.requestId, correctionApprove.value, correctionNote.value.trim())
    ElMessage.success('已决议并留痕审计')
    correctionDialog.visible = false
    await loadDataRights()
    await loadLogs()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '决议失败')
  } finally {
    correctionSubmitting.value = false
  }
}

const DISPUTE_STATUS_LABELS: Record<ClaimDispute['status'], string> = {
  open: '待处理',
  resolved_claim: '认领成立',
  resolved_reject: '驳回',
  withdrawn: '已撤回',
}

const openDisputes = computed(() => disputes.value.filter((d) => d.status === 'open'))

function openDisputeDialog(disputeId: number): void {
  disputeDialog.disputeId = disputeId
  disputeOutcome.value = 'resolved_claim'
  disputeNote.value = ''
  disputeDialog.visible = true
}

async function submitDisputeResolution(): Promise<void> {
  if (!disputeNote.value.trim()) return
  disputeSubmitting.value = true
  try {
    await resolveClaimDispute(disputeDialog.disputeId, disputeOutcome.value, disputeNote.value.trim())
    ElMessage.success('已决议并留痕审计')
    disputeDialog.visible = false
    await loadDisputes()
    await loadLogs()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '决议失败')
  } finally {
    disputeSubmitting.value = false
  }
}
</script>

<template>
  <main class="admin-view" v-loading="loading">
    <h2 class="title">平台运营后台</h2>
    <el-alert type="info" :closable="false" class="scope-hint" data-test="operator-scope-hint">
      平台运营者仅管理系统与安全策略，无家庭数据浏览权。数据兑底操作均需填写理由并完整审计。
    </el-alert>

    <!-- 用户管理 -->
    <section>
      <h3>用户管理</h3>
      <el-table :data="users" size="small" data-test="admin-user-table">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column prop="name" label="名字" />
        <el-table-column label="账号状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" size="small" type="danger">平台运营</el-tag>
            <el-tag v-else size="small" :type="row.claim_status === 'claimed' ? 'success' : 'warning'">
              {{ row.claim_status === 'claimed' ? '已认领' : '待认领' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="确档状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.profile_status === 'identity_confirmed' ? 'success' : 'info'">
              {{ row.profile_status === 'identity_confirmed' ? '已确档' : '待确档' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="privacy_mode" label="归属" width="110" />
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button size="small" type="warning" :data-test="`reset-pin-${row.id}`" @click="resetPin(row)">
              重置 PIN
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- owner onboarding 邀请 -->
    <section>
      <h3>Owner Onboarding 邀请</h3>
      <p class="section-desc">
        签发短期、单次、可撤销的邀请链接。对方登录后凭 token 兑换，将获得独立族谱空间并成为其所有者。
      </p>
      <el-button type="primary" :loading="issuing" data-test="issue-invitation" @click="issueInvitation">
        签发邀请
      </el-button>

      <el-dialog :model-value="issuedToken !== ''" title="邀请链接（仅显示一次）" width="460px" @update:model-value="issuedToken = ''">
        <p>请将以下 token 转交给新空间所有者：</p>
        <p class="big-token" data-test="issued-token">{{ issuedToken }}</p>
        <p class="hint">有效期至 {{ issuedExpiresAt }}；服务端只存哈希，关闭后不可回看。</p>
      </el-dialog>

      <el-table :data="invitations" size="small" class="mt8" data-test="invitation-table">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="过期时间" width="160">
          <template #default="{ row }">{{ row.expires_at?.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="invitationStatus(row).type" :data-test="`invitation-status-${row.id}`">
              {{ invitationStatus(row).text }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button
              v-if="!row.used_at && !row.revoked_at"
              size="small"
              type="danger"
              plain
              :data-test="`revoke-invitation-${row.id}`"
              @click="revokeInvitation(row.id)"
            >
              撤销
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <!-- 数据权利请求 -->
    <section>
      <h3>数据权利请求</h3>
      <p class="section-desc">导出由系统异步生成并过期；删除由本人自助执行；更正需运营者决议（break-glass）。</p>
      <el-table :data="dataRights" size="small" data-test="data-right-table">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column label="类型" width="80">
          <template #default="{ row }">{{ DATA_RIGHT_TYPE_LABELS[row.type as keyof typeof DATA_RIGHT_TYPE_LABELS] }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'pending' ? 'warning' : 'info'" :data-test="`dr-status-${row.id}`">
              {{ DATA_RIGHT_STATUS_LABELS[row.status as keyof typeof DATA_RIGHT_STATUS_LABELS] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button
              v-if="row.type === 'correct' && row.status === 'pending'"
              size="small"
              type="primary"
              plain
              :data-test="`resolve-correction-${row.id}`"
              @click="openCorrectionDialog(row.id)"
            >
              决议更正
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="resolvableCorrections.length === 0" class="hint">当前没有待决议的更正申请。</p>
    </section>

    <!-- 认领争议 -->
    <section>
      <h3>认领争议</h3>
      <p class="section-desc">证据原文保留在系统中，此处仅展示最小披露信息；决议需理由必填。</p>
      <el-table :data="disputes" size="small" data-test="dispute-table">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column prop="profile_id" label="涉及档案" width="90" />
        <el-table-column prop="raised_by_account_id" label="发起账号" width="90" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'open' ? 'warning' : 'info'" :data-test="`dispute-status-${row.id}`">
              {{ DISPUTE_STATUS_LABELS[row.status as keyof typeof DISPUTE_STATUS_LABELS] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 16) }}</template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'open'"
              size="small"
              type="primary"
              plain
              :data-test="`resolve-dispute-${row.id}`"
              @click="openDisputeDialog(row.id)"
            >
              决议
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-if="openDisputes.length === 0" class="hint">当前没有待处理的认领争议。</p>
    </section>

    <!-- 审计日志 -->
    <section>
      <h3>审计日志</h3>
      <ul class="audit-list" data-test="audit-list">
        <li v-for="log in logs.slice(0, 100)" :key="log.id" class="audit-row">
          <span class="time">{{ log.created_at?.slice(0, 19) }}</span>
          <span class="action">{{ log.action }}</span>
          <span class="meta">actor=#{{ log.actor_id ?? '-' }} target=#{{ log.target_id ?? '-' }}</span>
        </li>
      </ul>
    </section>

    <!-- 更正决议弹窗（break-glass：理由必填） -->
    <el-dialog
      v-model="correctionDialog.visible"
      title="决议资料更正申请"
      width="420px"
      data-test="correction-dialog"
    >
      <el-form label-position="top">
        <el-form-item label="决议">
          <el-radio-group v-model="correctionApprove" data-test="correction-approve-group">
            <el-radio :value="true">批准（按申请字段应用更正）</el-radio>
            <el-radio :value="false">驳回</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理理由（必填，写入审计）" required>
          <el-input
            v-model="correctionNote"
            type="textarea"
            :rows="3"
            maxlength="1000"
            placeholder="说明批准/驳回依据"
            data-test="correction-note-input"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="correctionDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!correctionNote.trim()"
          :loading="correctionSubmitting"
          data-test="correction-submit"
          @click="submitCorrection"
        >
          提交决议
        </el-button>
      </template>
    </el-dialog>

    <!-- 争议决议弹窗（break-glass：理由必填） -->
    <el-dialog
      v-model="disputeDialog.visible"
      title="决议认领争议"
      width="420px"
      data-test="dispute-resolve-dialog"
    >
      <el-form label-position="top">
        <el-form-item label="决议结果">
          <el-radio-group v-model="disputeOutcome" data-test="dispute-outcome-group">
            <el-radio value="resolved_claim">认领成立（档案归属移交申请人）</el-radio>
            <el-radio value="resolved_reject">驳回认领</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="处理理由（必填，写入审计）" required>
          <el-input
            v-model="disputeNote"
            type="textarea"
            :rows="3"
            maxlength="1000"
            placeholder="依据申请人提交的证据说明结论"
            data-test="dispute-note-input"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="disputeDialog.visible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!disputeNote.trim()"
          :loading="disputeSubmitting"
          data-test="dispute-resolution-submit"
          @click="submitDisputeResolution"
        >
          提交决议
        </el-button>
      </template>
    </el-dialog>

    <!-- 一次性 PIN 弹窗 -->
    <el-dialog :model-value="oneTimePin !== ''" title="新 PIN（仅显示一次）" width="380px" @update:model-value="oneTimePin = ''">
      <p>「{{ oneTimeFor }}」的新 PIN：</p>
      <p class="big-pin" data-test="one-time-admin-pin">{{ oneTimePin }}</p>
      <p class="hint">该成员下次登录将强制修改。请立即转交并截图保存。</p>
    </el-dialog>
  </main>
</template>

<style scoped>
.admin-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}

.title {
  font-size: 20px;
}

.scope-hint {
  margin-bottom: 20px;
}

section {
  margin-bottom: 32px;
}

.section-desc {
  margin: 4px 0 10px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.mt8 {
  margin-top: 8px;
}

.big-pin {
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 8px;
  text-align: center;
  color: var(--el-color-warning);
}

.big-token {
  padding: 10px;
  font-family: monospace;
  font-size: 14px;
  word-break: break-all;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  user-select: all;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.audit-list {
  list-style: none;
  padding: 0;
  max-height: 420px;
  overflow: auto;
}

.audit-row {
  display: flex;
  gap: 12px;
  font-size: 13px;
  padding: 4px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.time {
  color: var(--el-text-color-secondary);
  font-family: monospace;
}

.action {
  font-weight: 600;
  min-width: 180px;
}
</style>
