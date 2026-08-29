<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NDataTable,
  NDatePicker,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'
import type {
  InputHTMLAttributes as VueInputHTMLAttributes,
  TextareaHTMLAttributes as VueTextareaHTMLAttributes,
} from 'vue'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useGovernanceStore } from '@/stores/governance'
import type { CorrectableField, DataRightRequest } from '@/types/api'

/**
 * 我的数据面板（v2 F-5）：自助导出 / 更正 / 删除注销申请 + 认领争议入口。
 * 所有请求状态可追溯；导出产物过期后不可下载（后端 410）。
 * 请求状态徽章与全站领域状态同源（--fg-status-*，design.md §3.4）。
 */
const auth = useAuthStore()
const governance = useGovernanceStore()
const message = useMessage()

const requestingExport = ref(false)

// ---- 更正申请弹窗 ----
const correctDialogVisible = ref(false)
const correctField = ref<CorrectableField>('name')
const correctValueName = ref('')
const correctValueGender = ref<'m' | 'f' | 'unknown'>('unknown')
const correctValueBio = ref('')
const correctValueDate = ref('')
const correcting = ref(false)

// ---- 删除/注销 ----
const deleteDialogVisible = ref(false)
const deleteConfirmName = ref('')
const deleting = ref(false)
const deleteError = ref('')

// data-* 未收录进 Vue 的 HTML 属性类型，断言收窄；运行时 naive 原样透传到原生 input
const deleteConfirmInputProps = {
  'data-test': 'delete-request-confirm-input',
} as VueInputHTMLAttributes

const disputeEvidenceInputProps = {
  'data-test': 'dispute-evidence-input',
  'aria-label': '争议情况描述',
} as VueTextareaHTMLAttributes

// ---- 认领争议 ----
const disputeDialogVisible = ref(false)
const disputeEvidence = ref('')
const raisingDispute = ref(false)

onMounted(() => {
  governance.loadDataRights().catch(() => undefined)
  governance.loadDisputes().catch(() => undefined)
})

const TYPE_LABELS: Record<DataRightRequest['type'], string> = {
  export: '导出',
  correct: '更正',
  delete: '删除',
}
const STATUS_LABELS: Record<DataRightRequest['status'], string> = {
  pending: '排队中',
  processing: '生成中',
  completed: '已完成',
  rejected: '已驳回',
  expired: '已过期',
}

/** 请求状态 → 领域状态徽章（--fg-status-* 同源，design.md §3.4） */
function statusBadgeClass(status: DataRightRequest['status']): string {
  switch (status) {
    case 'completed':
      return 'fg-badge fg-badge--confirmed'
    case 'rejected':
      return 'fg-badge fg-badge--disputed'
    case 'expired':
      return 'fg-badge fg-badge--neutral'
    default:
      // pending / processing：进行中 = proposed 空心
      return 'fg-badge fg-badge--proposed'
  }
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

const historyColumns = computed<DataTableColumns<DataRightRequest>>(() => [
  { title: '编号', key: 'id', width: 60 },
  {
    title: '类型',
    key: 'type',
    width: 64,
    render: (row) => TYPE_LABELS[row.type],
  },
  {
    title: '状态',
    key: 'status',
    width: 84,
    render: (row) =>
      h(
        'span',
        { class: statusBadgeClass(row.status), 'data-test': `dr-status-${row.id}` },
        STATUS_LABELS[row.status],
      ),
  },
  { title: '创建时间', key: 'created_at', width: 130, render: (row) => formatTime(row.created_at) },
  { title: '过期时间', key: 'expires_at', width: 130, render: (row) => formatTime(row.expires_at) },
  {
    title: '操作',
    key: 'actions',
    render: (row) =>
      downloadableExports.value.some((e) => e.id === row.id)
        ? h(
            NButton,
            {
              size: 'tiny',
              type: 'primary',
              secondary: true,
              'data-test': `download-export-${row.id}`,
              onClick: () => download(row.id),
            },
            { default: () => '下载导出文件' },
          )
        : null,
  },
])

async function doRequestExport(): Promise<void> {
  requestingExport.value = true
  try {
    await governance.requestExport()
    message.success('导出申请已提交，完成后可在列表下载（产物有过期时间）')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '申请失败，请稍后重试')
  } finally {
    requestingExport.value = false
  }
}

/** 已完成且未过期的导出可下载 */
const downloadableExports = computed(() =>
  governance.dataRights.filter((r) => r.type === 'export' && r.status === 'completed'),
)

async function download(requestId: number): Promise<void> {
  try {
    const blob = await governance.downloadExport(requestId)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `familygraph-export-${requestId}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'DATA_RIGHT_REQUEST_EXPIRED') {
      message.warning('该导出文件已过期，请重新申请')
      await governance.loadDataRights().catch(() => undefined)
    } else {
      message.error('下载失败，请稍后重试')
    }
  }
}

function openCorrectDialog(): void {
  correctField.value = 'name'
  correctValueName.value = ''
  correctValueGender.value = 'unknown'
  correctValueBio.value = ''
  correctValueDate.value = ''
  correctDialogVisible.value = true
}

function buildCorrectionFields(): Record<string, unknown> | null {
  switch (correctField.value) {
    case 'name':
      return correctValueName.value.trim() ? { name: correctValueName.value.trim() } : null
    case 'gender':
      return { gender: correctValueGender.value }
    case 'bio':
      return { bio: correctValueBio.value }
    case 'birth':
    case 'death':
      // 更正按公历日期提交；历别换算由后端统一处理
      return correctValueDate.value
        ? { [correctField.value]: { cal_type: 'solar', date: correctValueDate.value } }
        : null
    default:
      return null
  }
}

async function submitCorrection(): Promise<void> {
  const fields = buildCorrectionFields()
  if (!fields) {
    message.warning('请填写更正后的值')
    return
  }
  correcting.value = true
  try {
    await governance.requestCorrection(fields)
    message.success('更正申请已提交，等待平台人工决议（理由与审计留痕）')
    correctDialogVisible.value = false
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '申请失败，请稍后重试')
  } finally {
    correcting.value = false
  }
}

async function submitDeletion(): Promise<void> {
  deleting.value = true
  deleteError.value = ''
  try {
    const request = await governance.requestDeletion()
    await governance.executeDelete(request.id, deleteConfirmName.value.trim())
    message.success('档案与账号已删除。感谢你曾使用 FamilyGraph。')
    deleteDialogVisible.value = false
    // 本地会话即刻失效：清空全部缓存并回登录页
    auth.clearSession()
    window.location.assign('/login')
  } catch (error) {
    deleteError.value =
      error instanceof ApiError
        ? error.code === 'CONFIRM_NAME_MISMATCH'
          ? '输入的名字与档案名字不一致'
          : error.message
        : '删除失败，请稍后重试'
  } finally {
    deleting.value = false
  }
}

async function raiseDispute(): Promise<void> {
  if (!disputeEvidence.value.trim() || auth.user === null) return
  raisingDispute.value = true
  try {
    await governance.raiseDispute(auth.user.id, { description: disputeEvidence.value.trim() })
    message.success('争议已提交，平台将人工复核并保留双方证据原文')
    disputeDialogVisible.value = false
    disputeEvidence.value = ''
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    raisingDispute.value = false
  }
}

async function withdraw(disputeId: number): Promise<void> {
  try {
    await governance.withdrawDispute(disputeId)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '撤回失败')
  }
}

const DISPUTE_STATUS_LABELS: Record<string, string> = {
  open: '复核中',
  resolved_claim: '认领成立',
  resolved_reject: '已驳回',
  withdrawn: '已撤回',
}

const correctFieldOptions: SelectOption[] = [
  { label: '名字', value: 'name' },
  { label: '性别', value: 'gender' },
  { label: '出生日期', value: 'birth' },
  { label: '去世日期', value: 'death' },
  { label: '简介', value: 'bio' },
]
</script>

<template>
  <section class="data-rights" data-test="data-rights-panel">
    <!-- 申请区 -->
    <div class="request-row">
      <NButton type="primary" secondary :loading="requestingExport" data-test="request-export-btn" @click="doRequestExport">
        申请数据导出
      </NButton>
      <NButton secondary data-test="open-correct-dialog" @click="openCorrectDialog">申请资料更正</NButton>
      <NButton type="error" secondary data-test="open-delete-dialog" @click="deleteDialogVisible = true">
        申请删除 / 注销
      </NButton>
      <NButton secondary data-test="open-dispute-dialog" @click="disputeDialogVisible = true">
        提交认领争议
      </NButton>
    </div>

    <!-- 申请历史 -->
    <h4 class="block-title">申请记录</h4>
    <NDataTable
      size="small"
      :columns="historyColumns"
      :data="governance.dataRights"
      :row-key="(row: DataRightRequest) => row.id"
      data-test="data-right-history"
    >
      <template #empty>
        <NEmpty description="暂无申请" size="small" />
      </template>
    </NDataTable>

    <!-- 我的争议 -->
    <template v-if="governance.disputes.length > 0">
      <h4 class="block-title">我的认领争议</h4>
      <ul class="dispute-list" data-test="my-dispute-list">
        <li v-for="d in governance.disputes" :key="d.id" class="dispute-row">
          <span>#{{ d.id }} · {{ DISPUTE_STATUS_LABELS[d.status] ?? d.status }} · {{ formatTime(d.created_at) }}</span>
          <NButton v-if="d.status === 'open'" size="tiny" secondary @click="withdraw(d.id)">撤回</NButton>
        </li>
      </ul>
    </template>

    <!-- 更正申请弹窗 -->
    <NModal v-model:show="correctDialogVisible" preset="card" title="申请资料更正" data-test="correct-dialog">
      <NForm label-placement="top" :show-feedback="false">
        <NFormItem label="选择字段">
          <NSelect v-model:value="correctField" :options="correctFieldOptions" data-test="correct-field-select" />
        </NFormItem>
        <NFormItem label="更正后的值">
          <NInput v-if="correctField === 'name'" v-model:value="correctValueName" :maxlength="100" data-test="correct-value-name" />
          <NRadioGroup v-else-if="correctField === 'gender'" v-model:value="correctValueGender" data-test="correct-value-gender">
            <NRadio value="f">女</NRadio>
            <NRadio value="m">男</NRadio>
            <NRadio value="unknown">不详</NRadio>
          </NRadioGroup>
          <NDatePicker
            v-else-if="correctField === 'birth' || correctField === 'death'"
            :formatted-value="correctValueDate || null"
            type="date"
            value-format="yyyy-MM-dd"
            placeholder="公历日期"
            data-test="correct-value-date"
            @update:formatted-value="(v: string | null) => (correctValueDate = v ?? '')"
          />
          <NInput v-else v-model:value="correctValueBio" type="textarea" :rows="3" :maxlength="2000" data-test="correct-value-bio" />
        </NFormItem>
        <p class="hint">批准后将由平台按白名单字段应用更正；处理需 break-glass 理由并完整审计。</p>
      </NForm>
      <template #footer>
        <div class="footer-actions">
          <NButton @click="correctDialogVisible = false">取消</NButton>
          <NButton type="primary" :loading="correcting" data-test="correct-submit" @click="submitCorrection">
            提交申请
          </NButton>
        </div>
      </template>
    </NModal>

    <!-- 删除/注销确认弹窗 -->
    <NModal v-model:show="deleteDialogVisible" preset="card" title="申请删除 / 注销" data-test="delete-request-dialog">
      <NAlert type="error" :show-icon="true" class="mb8">
        删除不可恢复：档案、账号、会话、附件与空间引用将一并移除；涉及的空间所有权必须先完成移交。
        导出缓存中的副本随备份轮转淘汰。
      </NAlert>
      <p class="confirm-text">
        请输入你的名字 <strong>{{ auth.user?.name }}</strong> 以确认：
      </p>
      <NInput v-model:value="deleteConfirmName" placeholder="输入名字确认" :input-props="deleteConfirmInputProps" />
      <p v-if="deleteError" class="error" data-test="delete-request-error">{{ deleteError }}</p>
      <template #footer>
        <div class="footer-actions">
          <NButton @click="deleteDialogVisible = false">取消</NButton>
          <NButton
            type="error"
            :disabled="deleteConfirmName !== auth.user?.name"
            :loading="deleting"
            data-test="delete-request-submit"
            @click="submitDeletion"
          >
            确认删除并注销
          </NButton>
        </div>
      </template>
    </NModal>

    <!-- 认领争议弹窗 -->
    <NModal v-model:show="disputeDialogVisible" preset="card" title="提交认领争议" data-test="raise-dispute-dialog">
      <p class="hint mb8">
        若你认为某档案归属存在错误（例如他人以你名义建档），可提交争议说明。证据原文将被保留，平台人工复核需记录理由与审计。
      </p>
      <NInput
        v-model:value="disputeEvidence"
        type="textarea"
        :rows="4"
        :maxlength="1000"
        placeholder="描述情况：涉及谁、错在哪里、依据是什么"
        :input-props="disputeEvidenceInputProps"
      />
      <template #footer>
        <div class="footer-actions">
          <NButton @click="disputeDialogVisible = false">取消</NButton>
          <NButton
            type="primary"
            :disabled="!disputeEvidence.trim()"
            :loading="raisingDispute"
            data-test="raise-dispute-submit"
            @click="raiseDispute"
          >
            提交争议
          </NButton>
        </div>
      </template>
    </NModal>
  </section>
</template>

<style scoped>
.request-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.block-title {
  margin: 12px 0 8px;
  font-size: 14px;
  color: var(--fg-ink);
}

.dispute-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.dispute-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 13px;
  color: var(--fg-ink);
  border-bottom: 1px solid var(--fg-line);
}

.confirm-text {
  margin: 0 0 10px;
  line-height: 1.6;
}

.mb8 {
  margin-bottom: 8px;
}

.hint {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.error {
  margin-top: 8px;
  color: var(--fg-status-disputed);
  font-size: 13px;
}

.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* 弹窗宽度：n-modal 根节点 teleport 到 body，以 data-test 锚定 */
[data-test='correct-dialog'],
[data-test='delete-request-dialog'],
[data-test='raise-dispute-dialog'] {
  width: min(420px, calc(100vw - 48px));
}
</style>
