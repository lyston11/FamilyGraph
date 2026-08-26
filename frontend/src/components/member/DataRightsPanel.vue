<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useGovernanceStore } from '@/stores/governance'
import type { CorrectableField, DataRightRequest } from '@/types/api'

/**
 * 我的数据面板（v2 F-5）：自助导出 / 更正 / 删除注销申请 + 认领争议入口。
 * 所有请求状态可追溯；导出产物过期后不可下载（后端 410）。
 */
const auth = useAuthStore()
const governance = useGovernanceStore()

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

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

async function doRequestExport(): Promise<void> {
  requestingExport.value = true
  try {
    await governance.requestExport()
    ElMessage.success('导出申请已提交，完成后可在列表下载（产物有过期时间）')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '申请失败，请稍后重试')
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
      ElMessage.warning('该导出文件已过期，请重新申请')
      await governance.loadDataRights().catch(() => undefined)
    } else {
      ElMessage.error('下载失败，请稍后重试')
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
    ElMessage.warning('请填写更正后的值')
    return
  }
  correcting.value = true
  try {
    await governance.requestCorrection(fields)
    ElMessage.success('更正申请已提交，等待平台人工决议（理由与审计留痕）')
    correctDialogVisible.value = false
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '申请失败，请稍后重试')
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
    ElMessage.success('档案与账号已删除。感谢你曾使用 FamilyGraph。')
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
    ElMessage.success('争议已提交，平台将人工复核并保留双方证据原文')
    disputeDialogVisible.value = false
    disputeEvidence.value = ''
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    raisingDispute.value = false
  }
}

async function withdraw(disputeId: number): Promise<void> {
  try {
    await governance.withdrawDispute(disputeId)
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '撤回失败')
  }
}

const DISPUTE_STATUS_LABELS: Record<string, string> = {
  open: '复核中',
  resolved_claim: '认领成立',
  resolved_reject: '已驳回',
  withdrawn: '已撤回',
}
</script>

<template>
  <section class="data-rights" data-test="data-rights-panel">
    <!-- 申请区 -->
    <div class="request-row">
      <el-button type="primary" plain :loading="requestingExport" data-test="request-export-btn" @click="doRequestExport">
        申请数据导出
      </el-button>
      <el-button plain data-test="open-correct-dialog" @click="openCorrectDialog">申请资料更正</el-button>
      <el-button type="danger" plain data-test="open-delete-dialog" @click="deleteDialogVisible = true">
        申请删除 / 注销
      </el-button>
      <el-button plain data-test="open-dispute-dialog" @click="disputeDialogVisible = true">
        提交认领争议
      </el-button>
    </div>

    <!-- 申请历史 -->
    <h4 class="block-title">申请记录</h4>
    <el-table :data="governance.dataRights" size="small" empty-text="暂无申请" data-test="data-right-history">
      <el-table-column prop="id" label="编号" width="70" />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">{{ TYPE_LABELS[row.type as keyof typeof TYPE_LABELS] }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'completed' ? 'success' : row.status === 'rejected' ? 'danger' : 'info'" :data-test="`dr-status-${row.id}`">
            {{ STATUS_LABELS[row.status as keyof typeof STATUS_LABELS] }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="140">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="过期时间" width="140">
        <template #default="{ row }">{{ formatTime(row.expires_at) }}</template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button
            v-if="downloadableExports.some((e) => e.id === row.id)"
            size="small"
            type="primary"
            plain
            :data-test="`download-export-${row.id}`"
            @click="download(row.id)"
          >
            下载导出文件
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 我的争议 -->
    <template v-if="governance.disputes.length > 0">
      <h4 class="block-title">我的认领争议</h4>
      <ul class="dispute-list" data-test="my-dispute-list">
        <li v-for="d in governance.disputes" :key="d.id" class="dispute-row">
          <span>#{{ d.id }} · {{ DISPUTE_STATUS_LABELS[d.status] ?? d.status }} · {{ formatTime(d.created_at) }}</span>
          <el-button v-if="d.status === 'open'" size="small" plain @click="withdraw(d.id)">撤回</el-button>
        </li>
      </ul>
    </template>

    <!-- 更正申请弹窗 -->
    <el-dialog v-model="correctDialogVisible" title="申请资料更正" width="420px" data-test="correct-dialog">
      <el-form label-position="top">
        <el-form-item label="选择字段">
          <el-select v-model="correctField" data-test="correct-field-select">
            <el-option label="名字" value="name" />
            <el-option label="性别" value="gender" />
            <el-option label="出生日期" value="birth" />
            <el-option label="去世日期" value="death" />
            <el-option label="简介" value="bio" />
          </el-select>
        </el-form-item>
        <el-form-item label="更正后的值">
          <el-input v-if="correctField === 'name'" v-model="correctValueName" maxlength="100" data-test="correct-value-name" />
          <el-radio-group v-else-if="correctField === 'gender'" v-model="correctValueGender" data-test="correct-value-gender">
            <el-radio value="f">女</el-radio>
            <el-radio value="m">男</el-radio>
            <el-radio value="unknown">不详</el-radio>
          </el-radio-group>
          <el-date-picker
            v-else-if="correctField === 'birth' || correctField === 'death'"
            v-model="correctValueDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="公历日期"
            data-test="correct-value-date"
          />
          <el-input v-else v-model="correctValueBio" type="textarea" :rows="3" maxlength="2000" data-test="correct-value-bio" />
        </el-form-item>
        <p class="hint">批准后将由平台按白名单字段应用更正；处理需 break-glass 理由并完整审计。</p>
      </el-form>
      <template #footer>
        <el-button @click="correctDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="correcting" data-test="correct-submit" @click="submitCorrection">
          提交申请
        </el-button>
      </template>
    </el-dialog>

    <!-- 删除/注销确认弹窗 -->
    <el-dialog v-model="deleteDialogVisible" title="申请删除 / 注销" width="420px" data-test="delete-request-dialog">
      <el-alert type="error" :closable="false" class="mb8">
        删除不可恢复：档案、账号、会话、附件与空间引用将一并移除；涉及的空间所有权必须先完成移交。
        导出缓存中的副本随备份轮转淘汰。
      </el-alert>
      <p class="confirm-text">
        请输入你的名字 <strong>{{ auth.user?.name }}</strong> 以确认：
      </p>
      <el-input v-model="deleteConfirmName" placeholder="输入名字确认" data-test="delete-request-confirm-input" />
      <p v-if="deleteError" class="error" data-test="delete-request-error">{{ deleteError }}</p>
      <template #footer>
        <el-button @click="deleteDialogVisible = false">取消</el-button>
        <el-button
          type="danger"
          :disabled="deleteConfirmName !== auth.user?.name"
          :loading="deleting"
          data-test="delete-request-submit"
          @click="submitDeletion"
        >
          确认删除并注销
        </el-button>
      </template>
    </el-dialog>

    <!-- 认领争议弹窗 -->
    <el-dialog v-model="disputeDialogVisible" title="提交认领争议" width="420px" data-test="raise-dispute-dialog">
      <p class="hint mb8">
        若你认为某档案归属存在错误（例如他人以你名义建档），可提交争议说明。证据原文将被保留，平台人工复核需记录理由与审计。
      </p>
      <el-input
        v-model="disputeEvidence"
        type="textarea"
        :rows="4"
        maxlength="1000"
        placeholder="描述情况：涉及谁、错在哪里、依据是什么"
        data-test="dispute-evidence-input"
      />
      <template #footer>
        <el-button @click="disputeDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!disputeEvidence.trim()"
          :loading="raisingDispute"
          data-test="raise-dispute-submit"
          @click="raiseDispute"
        >
          提交争议
        </el-button>
      </template>
    </el-dialog>
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
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.confirm-text {
  margin: 0 0 10px;
  line-height: 1.6;
}

.mb8 {
  margin-bottom: 8px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.error {
  margin-top: 8px;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
