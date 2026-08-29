<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSpin,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { TextareaHTMLAttributes } from 'vue'

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
import type { DataRightRequest, OwnerInvitation } from '@/types/api'

/**
 * 平台运营后台（v2 §0.2）：仅系统管理与 break-glass 数据兑底。
 * - 用户列表 / 重置 PIN / 审计时间线（原有）
 * - owner onboarding 邀请：签发（token 明文仅显示一次）/ 撤销
 * - 数据权利请求：更正决议（批准→按白名单字段应用；驳回）——理由必填 + 审计
 * - 认领争议决议：理由必填 + 审计，证据原文永不覆盖
 * operator 不提供任何家庭数据浏览权：本页不展示档案敏感字段。
 * P5 迁 naive-ui：n-data-table 列渲染走 h()；状态徽章与全站领域状态同源
 * （--fg-status-* 的 fg-badge 工具类，design.md §3.4）。
 */

const users = ref<AdminUserRow[]>([])
const logs = ref<AuditRow[]>([])
const invitations = ref<OwnerInvitation[]>([])
const dataRights = ref<DataRightRequest[]>([])
const disputes = ref<AdminClaimDisputeRow[]>([])
const loading = ref(false)
const oneTimePin = ref('')
const oneTimeFor = ref('')

const message = useMessage()
const dialog = useDialog()

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

// data-* 未收录进 Vue 的 HTML 属性类型，断言收窄；运行时 naive 原样透传到原生 textarea
const correctionNoteInputProps = { 'data-test': 'correction-note-input' } as TextareaHTMLAttributes
const disputeNoteInputProps = { 'data-test': 'dispute-note-input' } as TextareaHTMLAttributes

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
    message.error('加载失败（需要平台运营者身份）')
  } finally {
    loading.value = false
  }
})

async function resetPin(row: AdminUserRow): Promise<void> {
  // 列表不含家庭姓名（R-03），确认按账号 ID + 后端 confirm 标志 + 审计兜底
  dialog.warning({
    title: '重置 PIN',
    content: `确认重置账号 #${row.id} 的登录 PIN？该账号当前所有会话将立即失效，新 PIN 仅本次显示。`,
    positiveText: '确认重置',
    negativeText: '取消',
    onPositiveClick: () => {
      void (async () => {
        try {
          const { pin } = await adminResetPin(row.id)
          oneTimePin.value = pin
          oneTimeFor.value = String(row.id)
          await loadLogs()
        } catch (error) {
          message.error(error instanceof ApiError ? error.message : '重置失败，请稍后重试')
        }
      })()
    },
  })
}

/** 邀请状态 → 领域状态徽章（design.md §3.4：confirmed 实底 / disputed 朱砂 / 其余中性） */
function invitationBadge(inv: OwnerInvitation): { text: string; cls: string } {
  if (inv.used_at) return { text: '已兑换', cls: 'fg-badge fg-badge--neutral' }
  if (inv.revoked_at) return { text: '已撤销', cls: 'fg-badge fg-badge--disputed' }
  if (new Date(inv.expires_at).getTime() < Date.now())
    return { text: '已过期', cls: 'fg-badge fg-badge--provisional' }
  return { text: '有效', cls: 'fg-badge fg-badge--confirmed' }
}

async function issueInvitation(): Promise<void> {
  issuing.value = true
  try {
    const created = await createOwnerInvitation()
    issuedToken.value = created.token
    issuedExpiresAt.value = created.expires_at.replace('T', ' ').slice(0, 16)
    await loadInvitations()
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '签发失败')
  } finally {
    issuing.value = false
  }
}

async function revokeInvitation(id: number): Promise<void> {
  try {
    await revokeOwnerInvitation(id)
    await loadInvitations()
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '撤销失败')
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

/** 数据权利状态徽章（与 DataRightsPanel 同一映射：进行中=proposed 空心） */
function dataRightBadgeClass(status: DataRightRequest['status']): string {
  switch (status) {
    case 'completed':
      return 'fg-badge fg-badge--confirmed'
    case 'rejected':
      return 'fg-badge fg-badge--disputed'
    case 'expired':
      return 'fg-badge fg-badge--neutral'
    default:
      return 'fg-badge fg-badge--proposed'
  }
}

const userColumns = computed<DataTableColumns<AdminUserRow>>(() => [
  { title: 'ID', key: 'id', width: 64 },
  {
    title: '账号状态',
    key: 'account_status',
    width: 110,
    render: (row) =>
      row.is_admin
        ? h('span', { class: 'fg-badge fg-badge--accent' }, '平台运营')
        : h(
            'span',
            {
              class:
                row.claim_status === 'claimed'
                  ? 'fg-badge fg-badge--confirmed'
                  : 'fg-badge fg-badge--provisional',
            },
            row.claim_status === 'claimed' ? '已认领' : '待认领',
          ),
  },
  {
    title: '确档状态',
    key: 'profile_status',
    width: 110,
    render: (row) =>
      h(
        'span',
        {
          class:
            row.profile_status === 'identity_confirmed'
              ? 'fg-badge fg-badge--confirmed'
              : 'fg-badge fg-badge--provisional',
        },
        row.profile_status === 'identity_confirmed' ? '已确档' : '待确档',
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (row) =>
      h(
        NButton,
        {
          size: 'tiny',
          type: 'warning',
          secondary: true,
          'data-test': `reset-pin-${row.id}`,
          onClick: () => resetPin(row),
        },
        { default: () => '重置 PIN' },
      ),
  },
])

function formatTime(value: string | null | undefined): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

const invitationColumns = computed<DataTableColumns<OwnerInvitation>>(() => [
  { title: 'ID', key: 'id', width: 64 },
  { title: '创建时间', key: 'created_at', width: 160, render: (row) => formatTime(row.created_at) },
  { title: '过期时间', key: 'expires_at', width: 160, render: (row) => formatTime(row.expires_at) },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) => {
      const badge = invitationBadge(row)
      return h(
        'span',
        { class: badge.cls, 'data-test': `invitation-status-${row.id}` },
        badge.text,
      )
    },
  },
  {
    title: '操作',
    key: 'actions',
    width: 100,
    render: (row) =>
      !row.used_at && !row.revoked_at
        ? h(
            NButton,
            {
              size: 'tiny',
              type: 'error',
              secondary: true,
              'data-test': `revoke-invitation-${row.id}`,
              onClick: () => revokeInvitation(row.id),
            },
            { default: () => '撤销' },
          )
        : null,
  },
])

const dataRightColumns = computed<DataTableColumns<DataRightRequest>>(() => [
  { title: 'ID', key: 'id', width: 64 },
  {
    title: '类型',
    key: 'type',
    width: 80,
    render: (row) => DATA_RIGHT_TYPE_LABELS[row.type],
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) =>
      h(
        'span',
        {
          class: dataRightBadgeClass(row.status),
          'data-test': `dr-status-${row.id}`,
        },
        DATA_RIGHT_STATUS_LABELS[row.status],
      ),
  },
  { title: '创建时间', key: 'created_at', width: 160, render: (row) => formatTime(row.created_at) },
  {
    title: '操作',
    key: 'actions',
    render: (row) =>
      row.type === 'correct' && row.status === 'pending'
        ? h(
            NButton,
            {
              size: 'tiny',
              type: 'primary',
              secondary: true,
              'data-test': `resolve-correction-${row.id}`,
              onClick: () => openCorrectionDialog(row.id),
            },
            { default: () => '决议更正' },
          )
        : null,
  },
])

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
    message.success('已决议并留痕审计')
    correctionDialog.visible = false
    await loadDataRights()
    await loadLogs()
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '决议失败')
  } finally {
    correctionSubmitting.value = false
  }
}

const DISPUTE_STATUS_LABELS: Record<AdminClaimDisputeRow['status'], string> = {
  open: '待处理',
  resolved_claim: '认领成立',
  resolved_reject: '驳回',
  withdrawn: '已撤回',
}

const openDisputes = computed(() => disputes.value.filter((d) => d.status === 'open'))

const disputeColumns = computed<DataTableColumns<AdminClaimDisputeRow>>(() => [
  { title: 'ID', key: 'id', width: 64 },
  { title: '涉及档案', key: 'profile_id', width: 90 },
  { title: '发起账号', key: 'raised_by_account_id', width: 90 },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(
        'span',
        {
          class:
            row.status === 'open'
              ? 'fg-badge fg-badge--proposed'
              : row.status === 'resolved_claim'
                ? 'fg-badge fg-badge--confirmed'
                : row.status === 'resolved_reject'
                  ? 'fg-badge fg-badge--disputed'
                  : 'fg-badge fg-badge--neutral',
          'data-test': `dispute-status-${row.id}`,
        },
        DISPUTE_STATUS_LABELS[row.status],
      ),
  },
  { title: '创建时间', key: 'created_at', width: 160, render: (row) => formatTime(row.created_at) },
  {
    title: '操作',
    key: 'actions',
    render: (row) =>
      row.status === 'open'
        ? h(
            NButton,
            {
              size: 'tiny',
              type: 'primary',
              secondary: true,
              'data-test': `resolve-dispute-${row.id}`,
              onClick: () => openDisputeDialog(row.id),
            },
            { default: () => '决议' },
          )
        : null,
  },
])

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
    message.success('已决议并留痕审计')
    disputeDialog.visible = false
    await loadDisputes()
    await loadLogs()
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '决议失败')
  } finally {
    disputeSubmitting.value = false
  }
}
</script>

<template>
  <NSpin :show="loading">
    <main class="admin-view">
      <h2 class="title">平台运营后台</h2>
      <NAlert type="info" :show-icon="true" class="scope-hint" data-test="operator-scope-hint">
        平台运营者仅管理系统与安全策略，无家庭数据浏览权。数据兑底操作均需填写理由并完整审计。
      </NAlert>

      <!-- 用户管理 -->
      <section>
        <h3 class="section-title">用户管理</h3>
        <NDataTable
          size="small"
          :columns="userColumns"
          :data="users"
          :row-key="(row: AdminUserRow) => row.id"
          data-test="admin-user-table"
        />
      </section>

      <!-- owner onboarding 邀请 -->
      <section>
        <h3 class="section-title">Owner Onboarding 邀请</h3>
        <p class="section-desc">
          签发短期、单次、可撤销的邀请链接。对方登录后凭 token 兑换，将获得独立族谱空间并成为其所有者。
        </p>
        <NButton type="primary" :loading="issuing" data-test="issue-invitation" @click="issueInvitation">
          签发邀请
        </NButton>

        <NModal
          :show="issuedToken !== ''"
          preset="card"
          title="邀请链接（仅显示一次）"
          data-test="issued-token-dialog"
          @update:show="issuedToken = ''"
        >
          <p>请将以下 token 转交给新空间所有者：</p>
          <p class="big-token" data-test="issued-token">{{ issuedToken }}</p>
          <p class="hint">有效期至 {{ issuedExpiresAt }}；服务端只存哈希，关闭后不可回看。</p>
        </NModal>

        <NDataTable
          class="mt8"
          size="small"
          :columns="invitationColumns"
          :data="invitations"
          :row-key="(row: OwnerInvitation) => row.id"
          data-test="invitation-table"
        />
      </section>

      <!-- 数据权利请求 -->
      <section>
        <h3 class="section-title">数据权利请求</h3>
        <p class="section-desc">导出由系统异步生成并过期；删除由本人自助执行；更正需运营者决议（break-glass）。</p>
        <NDataTable
          size="small"
          :columns="dataRightColumns"
          :data="dataRights"
          :row-key="(row: DataRightRequest) => row.id"
          data-test="data-right-table"
        />
        <p v-if="resolvableCorrections.length === 0" class="hint">当前没有待决议的更正申请。</p>
      </section>

      <!-- 认领争议 -->
      <section>
        <h3 class="section-title">认领争议</h3>
        <p class="section-desc">证据原文保留在系统中，此处仅展示最小披露信息；决议需理由必填。</p>
        <NDataTable
          size="small"
          :columns="disputeColumns"
          :data="disputes"
          :row-key="(row: AdminClaimDisputeRow) => row.id"
          data-test="dispute-table"
        />
        <p v-if="openDisputes.length === 0" class="hint">当前没有待处理的认领争议。</p>
      </section>

      <!-- 审计日志 -->
      <section>
        <h3 class="section-title">审计日志</h3>
        <ul class="audit-list" data-test="audit-list">
          <li v-for="log in logs.slice(0, 100)" :key="log.id" class="audit-row">
            <span class="time">{{ log.created_at?.slice(0, 19) }}</span>
            <span class="action">{{ log.action }}</span>
            <span class="meta">actor=#{{ log.actor_id ?? '-' }} target=#{{ log.target_id ?? '-' }}</span>
          </li>
        </ul>
      </section>

      <!-- 更正决议弹窗（break-glass：理由必填） -->
      <NModal
        v-model:show="correctionDialog.visible"
        preset="card"
        title="决议资料更正申请"
        data-test="correction-dialog"
      >
        <NForm label-placement="top" :show-feedback="false">
          <NFormItem label="决议">
            <NRadioGroup v-model:value="correctionApprove" data-test="correction-approve-group">
              <NRadio :value="true">批准（按申请字段应用更正）</NRadio>
              <NRadio :value="false">驳回</NRadio>
            </NRadioGroup>
          </NFormItem>
          <NFormItem label="处理理由（必填，写入审计）" required>
            <NInput
              v-model:value="correctionNote"
              type="textarea"
              :rows="3"
              :maxlength="1000"
              placeholder="说明批准/驳回依据"
              :input-props="correctionNoteInputProps"
            />
          </NFormItem>
        </NForm>
        <template #footer>
          <div class="footer-actions">
            <NButton @click="correctionDialog.visible = false">取消</NButton>
            <NButton
              type="primary"
              :disabled="!correctionNote.trim()"
              :loading="correctionSubmitting"
              data-test="correction-submit"
              @click="submitCorrection"
            >
              提交决议
            </NButton>
          </div>
        </template>
      </NModal>

      <!-- 争议决议弹窗（break-glass：理由必填） -->
      <NModal
        v-model:show="disputeDialog.visible"
        preset="card"
        title="决议认领争议"
        data-test="dispute-resolve-dialog"
      >
        <NForm label-placement="top" :show-feedback="false">
          <NFormItem label="决议结果">
            <NRadioGroup v-model:value="disputeOutcome" data-test="dispute-outcome-group">
              <NRadio value="resolved_claim">认领成立（档案归属移交申请人）</NRadio>
              <NRadio value="resolved_reject">驳回认领</NRadio>
            </NRadioGroup>
          </NFormItem>
          <NFormItem label="处理理由（必填，写入审计）" required>
            <NInput
              v-model:value="disputeNote"
              type="textarea"
              :rows="3"
              :maxlength="1000"
              placeholder="依据申请人提交的证据说明结论"
              :input-props="disputeNoteInputProps"
            />
          </NFormItem>
        </NForm>
        <template #footer>
          <div class="footer-actions">
            <NButton @click="disputeDialog.visible = false">取消</NButton>
            <NButton
              type="primary"
              :disabled="!disputeNote.trim()"
              :loading="disputeSubmitting"
              data-test="dispute-resolution-submit"
              @click="submitDisputeResolution"
            >
              提交决议
            </NButton>
          </div>
        </template>
      </NModal>

      <!-- 一次性 PIN 弹窗 -->
      <NModal
        :show="oneTimePin !== ''"
        preset="card"
        title="新 PIN（仅显示一次）"
        data-test="one-time-pin-dialog"
        @update:show="oneTimePin = ''"
      >
        <p>账号 #{{ oneTimeFor }} 的新 PIN：</p>
        <p class="big-pin" data-test="one-time-admin-pin">{{ oneTimePin }}</p>
        <p class="hint">该成员下次登录将强制修改。请立即转交并截图保存。</p>
      </NModal>
    </main>
  </NSpin>
</template>

<style scoped>
.admin-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px;
}

.title {
  margin: 0 0 12px;
  font-family: var(--fg-font-display);
  font-size: 20px;
  color: var(--fg-ink);
}

.scope-hint {
  margin-bottom: 20px;
}

section {
  margin-bottom: 32px;
}

.section-title {
  margin: 0 0 8px;
  font-family: var(--fg-font-display);
  font-size: 16px;
  color: var(--fg-ink);
}

.section-desc {
  margin: 4px 0 10px;
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.mt8 {
  margin-top: 8px;
}

/* 一次性凭据展示：与 OnboardingView 凭证卡同一视觉语言（显示字体 + 大字距） */
.big-pin {
  font-family: var(--fg-font-display);
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 8px;
  text-align: center;
  color: var(--fg-ink);
}

.big-token {
  padding: 10px;
  font-family: monospace;
  font-size: 14px;
  word-break: break-all;
  background: var(--fg-surface-sunken);
  border-radius: var(--fg-radius-control);
  user-select: all;
}

.hint {
  color: var(--fg-ink-secondary);
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
  border-bottom: 1px solid var(--fg-line);
}

.time {
  color: var(--fg-ink-secondary);
  font-family: monospace;
}

.action {
  font-weight: 600;
  min-width: 180px;
  color: var(--fg-ink);
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* 弹窗宽度：n-modal 根节点 teleport 到 body，以 data-test 锚定 */
[data-test='issued-token-dialog'],
[data-test='one-time-pin-dialog'],
[data-test='correction-dialog'],
[data-test='dispute-resolve-dialog'] {
  width: min(460px, calc(100vw - 48px));
}
</style>
