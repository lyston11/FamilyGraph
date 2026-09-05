<script setup lang="ts">
/**
 * 运营治理：空间管理员申请裁决队列。
 *
 * 审批是后台唯一业务写 UI：
 * - approve（理由可选）/ reject（理由必填）均需二次确认弹窗 + 不可逆提示；
 * - 调用携带 confirm: true；终态后展示不可改判并刷新列表；
 * - 页面不提供 PIN 重置、档案修改、删除/恢复、导出或附件下载入口。
 */
import { onMounted, ref, watch } from 'vue'
import { apiOperationsQueue } from '@/api/read'
import {
  apiApproveManagerApplication,
  apiRejectManagerApplication,
} from '@/api/governance'
import { AdminApiError } from '@/api/client'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import DecisionModal from '@/components/DecisionModal.vue'
import type { AdminOperationsQueueItemOut, AdminPageOut } from '@/types/api'

const data = ref<AdminPageOut<AdminOperationsQueueItemOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20
const statusFilter = ref<'' | 'pending' | 'approved' | 'rejected'>('pending')

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiOperationsQueue({
      page: page.value,
      pageSize,
      kind: 'manager_application',
      status: statusFilter.value || null,
    })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

watch([page, statusFilter], () => void load())
onMounted(load)

// ---- 裁决弹窗 ----
type Decision = {
  applicationId: number
  decision: 'approve' | 'reject'
  applicantName: string
  spaceName: string
}

const decisionModalVisible = ref(false)
const decision = ref<Decision | null>(null)
const deciding = ref(false)
const decisionError = ref<string | null>(null)
const decidedNotice = ref<string | null>(null)

const STATUS_LABELS: Record<string, string> = {
  pending: '待裁决',
  approved: '已批准',
  rejected: '已驳回',
}

function applicationStatus(item: AdminOperationsQueueItemOut): string {
  return STATUS_LABELS[item.status] ?? item.status
}

function openDecision(item: AdminOperationsQueueItemOut, action: 'approve' | 'reject'): void {
  decision.value = {
    applicationId: item.reference_id,
    decision: action,
    applicantName: item.applicant_name ?? `#${item.applicant_user_id ?? '-'}`,
    spaceName: item.space_name ?? `#${item.space_id ?? '-'}`,
  }
  decisionError.value = null
  decisionModalVisible.value = true
}

function closeDecision(): void {
  decisionModalVisible.value = false
  decision.value = null
  decisionError.value = null
}

async function onDecisionConfirm(note: string | null): Promise<void> {
  const current = decision.value
  if (!current || deciding.value) return
  deciding.value = true
  decisionError.value = null
  try {
    const result =
      current.decision === 'approve'
        ? await apiApproveManagerApplication(current.applicationId, note)
        : await apiRejectManagerApplication(current.applicationId, note ?? '')
    decidedNotice.value = `申请 #${result.id} 裁决完成：${STATUS_LABELS[result.status] ?? result.status}（终态，不可改判）`
    closeDecision()
    page.value = 1
    await load()
  } catch (error) {
    if (error instanceof AdminApiError) {
      // 终态重复裁决 409 / 理由缺失 422：统一展示服务端安全文案
      decisionError.value = error.message
    } else {
      decisionError.value = '裁决失败，请稍后重试'
    }
  } finally {
    deciding.value = false
  }
}

const canDecide = (item: AdminOperationsQueueItemOut): boolean =>
  item.kind === 'manager_application' && item.status === 'pending'
</script>

<template>
  <div>
    <h1 class="ag-page-title">运营治理</h1>
    <p class="ag-page-subtitle">
      空间管理员申请裁决。批准/驳回为终态操作，提交后不可撤销或改判。
    </p>

    <p
      v-if="decidedNotice"
      class="form-success"
      role="status"
      data-testid="decision-result"
    >
      {{ decidedNotice }}
    </p>

    <div class="ag-card">
      <div class="ag-toolbar">
        <label class="ag-filter-label">
          状态
          <select v-model="statusFilter" aria-label="按申请状态筛选" data-testid="operations-status">
            <option value="pending">待裁决</option>
            <option value="approved">已批准</option>
            <option value="rejected">已驳回</option>
            <option value="">全部</option>
          </select>
        </label>
      </div>

      <PageState v-if="state !== 'ready'" :state="state" empty-text="队列为空" @retry="load" />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="没有匹配的申请" />
        </div>
        <template v-else>
          <div class="ag-cards">
            <div
              v-for="item in data.items"
              :key="item.reference_id"
              class="ag-card-row"
              data-testid="application-item"
            >
              <div class="ag-card-row-title">
                申请 #{{ item.reference_id }}
                <span
                  class="ag-tag"
                  :class="item.status === 'pending' ? 'ag-tag-warning' : 'ag-tag-healthy'"
                >
                  {{ applicationStatus(item) }}
                </span>
              </div>
              <div class="ag-card-row-meta">
                申请人：{{ item.applicant_name ?? `#${item.applicant_user_id ?? '-'}` }} ·
                目标空间：{{ item.space_name ?? `#${item.space_id ?? '-'}` }}
                <template v-if="item.space_kind">（{{ item.space_kind === 'household' ? '家庭空间' : '族谱空间' }}）</template>
              </div>
              <div class="ag-card-row-meta">提交于 {{ item.created_at }}</div>
              <div v-if="canDecide(item)" class="ag-toolbar ag-toolbar-flush">
                <button
                  type="button"
                  class="ag-tag ag-btn-primary"
                  :data-testid="`approve-${item.reference_id}`"
                  @click="openDecision(item, 'approve')"
                >
                  批准
                </button>
                <button
                  type="button"
                  class="ag-tag ag-btn-danger"
                  :data-testid="`reject-${item.reference_id}`"
                  @click="openDecision(item, 'reject')"
                >
                  驳回
                </button>
              </div>
            </div>
          </div>
          <ListPagination
            :page="data.page"
            :page-size="data.page_size"
            :total="data.total"
            @update:page="page = $event"
          />
        </template>
      </template>
    </div>

    <DecisionModal
      :visible="decisionModalVisible"
      :decision="decision?.decision ?? 'approve'"
      :applicant-name="decision?.applicantName ?? ''"
      :space-name="decision?.spaceName ?? ''"
      :pending="deciding"
      :error-message="decisionError"
      @confirm="onDecisionConfirm"
      @close="closeDecision"
    />
  </div>
</template>
