<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NButton, NEmpty, NInput, NModal, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import {
  fetchEligibleManagerTargets,
  fetchMyManagerApplications,
  fetchMyTransferConsents,
  respondTransferConsent,
  submitManagerApplication,
} from '@/api/spaces'
import type {
  EligibleManagerTarget,
  ManagerTransferConsent,
  SpaceManagerApplication,
} from '@/types/api'

/**
 * 空间管理员申请与交接面板（自旧 HomeView 内联逻辑抽出，09-01 Phase 3 流程保全）。
 *
 * - 申请资格、候选目标、是否已有在办申请全部由服务端 eligible-targets 裁定，
 *   前端不按本地 role 推断；服务端没有候选时入口不出现（fail-closed）；
 * - 审批两步走：系统管理员先受理，再由目标空间原管理员在工单里同意；
 * - 本面板自管数据加载（挂载即拉取），可嵌入设置/空间管理等治理页面；
 * - 所有写操作经 api/spaces.ts 领域命令，成功后重新拉取服务端状态，无乐观更新。
 */
const message = useMessage()

const applyOpen = ref(false)
/** 正在提交的目标空间 id：按卡片粒度显示 loading，避免整个列表被锁住 */
const applyingSpaceId = ref<number | null>(null)
const myApplications = ref<SpaceManagerApplication[]>([])
const eligibleTargets = ref<EligibleManagerTarget[]>([])
const myConsents = ref<ManagerTransferConsent[]>([])
const consentReason = ref<Record<number, string>>({})
const respondingConsentId = ref<number | null>(null)

onMounted(() => {
  loadMyApplications()
  loadEligibleTargets()
  loadMyConsents()
})

async function loadMyApplications(): Promise<void> {
  try {
    myApplications.value = await fetchMyManagerApplications()
  } catch {
    /* 状态行加载失败不阻塞面板 */
  }
}

/** 可申请目标由服务端裁定；失败时置空 → 入口隐藏（fail-closed） */
async function loadEligibleTargets(): Promise<void> {
  try {
    eligibleTargets.value = await fetchEligibleManagerTargets()
  } catch {
    eligibleTargets.value = []
  }
}

/** 我作为原管理员待处理的交接工单（PRD R3 第二阶段） */
async function loadMyConsents(): Promise<void> {
  try {
    myConsents.value = (await fetchMyTransferConsents()).filter((c) => c.status === 'pending')
  } catch {
    myConsents.value = []
  }
}

function openApplyDialog(): void {
  applyOpen.value = true
  void loadEligibleTargets()
}

async function applyForTarget(target: EligibleManagerTarget): Promise<void> {
  applyingSpaceId.value = target.space_id
  try {
    await submitManagerApplication('space_admin', { spaceId: target.space_id })
    message.success('申请已提交，等待系统管理员受理与原管理员同意')
    applyOpen.value = false
    await Promise.all([loadMyApplications(), loadEligibleTargets()])
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    applyingSpaceId.value = null
  }
}

async function respondConsent(
  consent: ManagerTransferConsent,
  decision: 'accept' | 'reject',
): Promise<void> {
  const reason = (consentReason.value[consent.id] ?? '').trim()
  if (decision === 'reject' && !reason) {
    message.error('谢绝交接需要填写理由')
    return
  }
  respondingConsentId.value = consent.id
  try {
    await respondTransferConsent(consent.id, decision, reason || undefined)
    message.success(
      decision === 'accept' ? '已同意交接，等待系统管理员完成换人' : '已谢绝该交接申请',
    )
    delete consentReason.value[consent.id]
    await Promise.all([loadMyConsents()])
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  } finally {
    respondingConsentId.value = null
  }
}

function consentText(consent: ManagerTransferConsent): string {
  const applicant = consent.applicant_name ?? `用户 #${consent.applicant_user_id ?? '?'}`
  return `${applicant} 申请接手「${consent.space_name ?? `空间 #${consent.space_id}`}」的管理员`
}

function applicationText(app: SpaceManagerApplication): string {
  return `「${app.space_name ?? '目标空间'}」空间管理员申请`
}

/**
 * 申请状态徽章：与全站领域状态同源（--fg-status-* / fg-badge）。
 * pending 且工单已建立时区分出「待原管理员同意」，让申请人知道卡在哪一步。
 */
function applicationBadge(app: SpaceManagerApplication): { text: string; cls: string } {
  if (app.status === 'approved') return { text: '已通过', cls: 'fg-badge fg-badge--confirmed' }
  if (app.status === 'rejected') return { text: '未通过', cls: 'fg-badge fg-badge--disputed' }
  if (app.transfer_consent_status === 'pending') {
    return { text: '待原管理员同意', cls: 'fg-badge fg-badge--proposed' }
  }
  return { text: '审批中', cls: 'fg-badge fg-badge--proposed' }
}
</script>

<template>
  <section class="manager-application-panel" data-test="manager-application-panel">
    <!-- 入口只在服务端给出候选目标时出现：资格判定不在前端 -->
    <div v-if="eligibleTargets.length > 0" class="panel-entry">
      <NButton
        size="small"
        secondary
        data-test="apply-space-admin"
        @click="openApplyDialog"
      >申请接手族谱空间</NButton>
    </div>

    <!-- 原管理员交接工单：只有我本人是目标空间现任管理员时才会收到 -->
    <div
      v-for="consent in myConsents"
      :key="`consent-${consent.id}`"
      class="consent-row"
      data-test="transfer-consent-row"
    >
      <div class="consent-head">
        <span class="consent-text">{{ consentText(consent) }}</span>
        <span class="fg-badge fg-badge--proposed">待你决定</span>
      </div>
      <p class="consent-hint">
        同意后你将降为普通成员，该空间管理员由申请人接任；谢绝会终止这次申请。
      </p>
      <div class="consent-actions">
        <NInput
          v-model:value="consentReason[consent.id]"
          size="small"
          class="consent-reason"
          placeholder="谢绝需填写理由（同意可留空）"
          :data-test="`consent-reason-${consent.id}`"
        />
        <NButton
          size="small"
          type="primary"
          :loading="respondingConsentId === consent.id"
          :data-test="`consent-accept-${consent.id}`"
          @click="respondConsent(consent, 'accept')"
        >同意交接</NButton>
        <NButton
          size="small"
          secondary
          :loading="respondingConsentId === consent.id"
          :data-test="`consent-reject-${consent.id}`"
          @click="respondConsent(consent, 'reject')"
        >谢绝</NButton>
      </div>
    </div>

    <!-- 我的管理者申请状态（仅已有空间 space_admin 申请） -->
    <div
      v-for="app in myApplications"
      :key="app.id"
      class="application-row"
      data-test="manager-application-row"
    >
      <span class="application-text">
        {{ applicationText(app) }}
        <span v-if="app.status === 'rejected' && app.decision_note" class="application-note">
          平台备注：{{ app.decision_note }}
        </span>
      </span>
      <span class="fg-badge" :class="applicationBadge(app).cls">
        {{ applicationBadge(app).text }}
      </span>
    </div>
  </section>

  <NModal
    v-model:show="applyOpen"
    preset="card"
    title="申请接手族谱空间管理员"
    data-test="apply-space-admin-dialog"
  >
    <p class="apply-hint">
      选择你要接手的族谱空间。提交后由系统管理员受理，并需该空间现任管理员本人同意才会换人。
    </p>
    <NEmpty
      v-if="eligibleTargets.length === 0"
      size="small"
      description="当前没有可申请的族谱空间"
      data-test="apply-targets-empty"
    />
    <ul v-else class="target-list">
      <li
        v-for="target in eligibleTargets"
        :key="target.space_id"
        class="target-card"
        :data-test="`apply-target-${target.space_id}`"
      >
        <div class="target-main">
          <span class="target-name">{{ target.space_name }}</span>
          <span class="fg-badge fg-badge--accent">族谱空间</span>
        </div>
        <p class="target-meta">
          现任管理员：{{ target.current_manager_name ?? '暂无' }}
        </p>
        <NButton
          size="small"
          type="primary"
          secondary
          :disabled="target.has_pending_application"
          :loading="applyingSpaceId === target.space_id"
          :data-test="`apply-target-submit-${target.space_id}`"
          @click="applyForTarget(target)"
        >
          {{ target.has_pending_application ? '已有在办申请' : '申请接手' }}
        </NButton>
      </li>
    </ul>
    <template #footer>
      <div class="modal-actions">
        <NButton @click="applyOpen = false">关闭</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.manager-application-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.panel-entry {
  display: flex;
}

.application-row,
.consent-row {
  display: flex;
  gap: 12px;
}

.application-row {
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent);
  border-radius: var(--fg-radius-control);
}

.application-text {
  min-width: 0;
}

.application-note {
  display: block;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.consent-row {
  flex-direction: column;
  padding: 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent);
  border-radius: var(--fg-radius-control);
}

.consent-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.consent-text {
  min-width: 0;
}

.consent-hint {
  margin: 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
  line-height: 1.5;
}

.consent-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.consent-reason {
  flex: 1;
  min-width: 0;
}

.apply-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.target-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.target-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background-color: var(--fg-surface-sunken);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
}

.target-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.target-name {
  font-size: 14px;
  color: var(--fg-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-meta {
  margin: 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.target-card :deep(.n-button) {
  align-self: flex-start;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 560px) {
  .consent-actions {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='apply-space-admin-dialog'] {
  width: min(460px, calc(100vw - 48px));
}
</style>
