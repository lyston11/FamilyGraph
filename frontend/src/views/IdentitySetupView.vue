<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NEmpty, NInput, NModal, NSpin, useMessage } from 'naive-ui'
import type { TextareaHTMLAttributes } from 'vue'

import { ApiError } from '@/api/errors'
import { getSafeInternalRedirect } from '@/router/redirect'
import { useAuthStore } from '@/stores/auth'
import { useGovernanceStore } from '@/stores/governance'
import type { FactReview } from '@/types/api'

/**
 * 首登确档向导（沉浸页，meta.chrome='blank'；v2 F-1）：「这是我」合并确认 → 确档清单逐项确认/争议。
 *
 * - 第一步是 Account managed→claimed 与本人 Profile provisional→identity_confirmed
 *   的唯一合法联动（POST /me/identity/confirm）；已确认过则后端 409，视为通过。
 * - 第二步逐项决议 profile_fact_reviews：confirmed | disputed（终态）。
 * - 清单条目以 --fg-status-* 徽章表达领域状态：待核对（空心）/已确认（实底）/已提出争议（虚线警示）。
 * - 全部决议完成后放行进入主界面；路由守卫依据 governance store 判断。
 */
const auth = useAuthStore()
const governance = useGovernanceStore()
const router = useRouter()
const route = useRoute()
const message = useMessage()

/** 是否已完成「这是我」：以 /me 直出的 profile_status 为初始值（v2 Gap2），
 * 已确认者直接进入清单步骤；409 = 此前已确认，仍视为通过（兼容旧会话） */
const identityConfirmed = ref(auth.user?.profile_status === 'identity_confirmed')
const confirming = ref(false)
const loadingReviews = ref(true)
const errorMessage = ref('')

// 争议备注弹窗
const disputeTarget = ref<FactReview | null>(null)
const disputeNote = ref('')
const disputeSubmitting = ref(false)

// data-* 未收录进 Vue 的 HTML 属性类型，断言收窄；运行时 naive 原样透传到 textarea
const disputeNoteInputProps = {
  'data-test': 'dispute-note-input',
  'aria-label': '争议说明（选填）',
} as TextareaHTMLAttributes

onMounted(async () => {
  try {
    await governance.loadFactReviews()
  } finally {
    loadingReviews.value = false
  }
})

const pendingReviews = computed(() => governance.pendingFactReviews)
/** 清单全部决议（含无清单场景）即可完成 */
const canFinish = computed(
  () => !loadingReviews.value && identityConfirmed.value && pendingReviews.value.length === 0,
)

function itemTitle(review: FactReview): string {
  switch (review.item_type) {
    case 'name':
      return '名字'
    case 'gender':
      return '性别'
    case 'birth':
      return '出生信息'
    case 'death':
      return '去世信息'
    case 'bio':
      return '简介'
    case 'relation_to_creator': {
      const creator = review.item_ref_json['creator_name']
      return creator ? `与创建者（${String(creator)}）的关系` : '与创建者的关系'
    }
    default:
      return review.item_type
  }
}

function itemDetail(review: FactReview): string {
  const ref_ = review.item_ref_json
  if (review.item_type === 'name' && typeof ref_['value'] === 'string') {
    return `提议的名字：「${ref_['value']}」`
  }
  if (review.item_type === 'relation_to_creator' && typeof ref_['creator_name'] === 'string') {
    return `由 ${ref_['creator_name']} 为你建档并代管`
  }
  return '由建档人提供的资料，请核对是否属实。'
}

async function doConfirmIdentity(): Promise<void> {
  confirming.value = true
  errorMessage.value = ''
  try {
    await governance.confirmIdentity()
    identityConfirmed.value = true
  } catch (error) {
    if (error instanceof ApiError && error.code === 'IDENTITY_INVALID_TRANSITION') {
      // 此前已完成认领+确认：直接放行到清单步骤
      identityConfirmed.value = true
    } else {
      errorMessage.value = error instanceof ApiError ? error.message : '操作失败，请稍后重试'
    }
  } finally {
    confirming.value = false
  }
}

function askDispute(review: FactReview): void {
  disputeTarget.value = review
  disputeNote.value = ''
}

function onDisputeShowChange(show: boolean): void {
  if (!show) disputeTarget.value = null
}

async function submitDispute(): Promise<void> {
  const target = disputeTarget.value
  if (!target) return
  disputeSubmitting.value = true
  try {
    await governance.decideReviewItem(target.id, 'disputed', disputeNote.value.trim() || null)
    disputeTarget.value = null
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    disputeSubmitting.value = false
  }
}

async function confirmReview(review: FactReview): Promise<void> {
  try {
    await governance.decideReviewItem(review.id, 'confirmed')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  }
}

function finish(): void {
  const target = getSafeInternalRedirect(route.query.redirect) ?? '/'
  void router.replace(target)
}
</script>

<template>
  <main class="identity-setup-view">
    <section class="plate" data-test="setup-card">
      <h1 class="title">完善你的身份信息</h1>
      <p class="desc">首次登录需要确认档案归属，并核对家人为你填写的资料。</p>

      <!-- 第一步：这是我 -->
      <section v-if="!identityConfirmed" class="step" data-test="confirm-step">
        <h2 class="step-title">
          <span class="step-no" aria-hidden="true">1</span>这是我的档案
        </h2>
        <p class="meta">
          档案「{{ auth.user?.name }}」当前由家人代管。确认后账号归你所有，初始 PIN 将保持已更换状态。
        </p>
        <p v-if="errorMessage" class="error" role="alert" data-test="confirm-error">
          {{ errorMessage }}
        </p>
        <NButton
          class="step-action"
          type="primary"
          :loading="confirming"
          data-test="confirm-btn"
          @click="doConfirmIdentity"
        >
          这是我，确认归属
        </NButton>
      </section>

      <!-- 第二步：确档清单 -->
      <section v-else class="step" data-test="checklist-step">
        <h2 class="step-title">
          <span class="step-no" aria-hidden="true">2</span>核对资料清单
        </h2>
        <p class="meta">以下资料由建档人提供，逐条确认或提出争议。有争议的条目将保留原文供平台复核。</p>

        <NSpin :show="loadingReviews">
          <NEmpty
            v-if="!loadingReviews && governance.factReviews.length === 0"
            class="empty"
            description="没有待核对的资料"
            size="small"
          />
          <ul v-else class="review-list" data-test="review-list">
            <li
              v-for="review in governance.factReviews"
              :key="review.id"
              class="review-item"
              :data-test="`review-item-${review.id}`"
            >
              <div class="review-main">
                <div class="review-title-row">
                  <span class="review-title">{{ itemTitle(review) }}</span>
                  <!-- 待审核（proposed）：空心徽章，design.md §3.4 -->
                  <span v-if="review.status === 'proposed'" class="badge badge-proposed">
                    待核对
                  </span>
                </div>
                <span class="review-detail">{{ itemDetail(review) }}</span>
              </div>
              <div class="review-actions">
                <template v-if="review.status === 'proposed'">
                  <NButton
                    size="small"
                    type="primary"
                    secondary
                    :data-test="`review-confirm-${review.id}`"
                    @click="confirmReview(review)"
                  >
                    确认无误
                  </NButton>
                  <NButton
                    size="small"
                    type="error"
                    secondary
                    :data-test="`review-dispute-${review.id}`"
                    @click="askDispute(review)"
                  >
                    有争议
                  </NButton>
                </template>
                <!-- 已确认：实底徽章 -->
                <span v-else-if="review.status === 'confirmed'" class="badge badge-confirmed">
                  已确认
                </span>
                <!-- 有争议：朱砂虚线警示徽章 -->
                <span v-else class="badge badge-disputed">已提出争议</span>
              </div>
            </li>
          </ul>
        </NSpin>
      </section>

      <div v-if="canFinish" class="done-row">
        <p class="done-hint" data-test="done-hint">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path
              d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"
            />
          </svg>
          <span>资料核对完成！你已具备完整的身份状态。</span>
        </p>
        <NButton type="primary" block data-test="finish-btn" @click="finish">
          进入 FamilyGraph
        </NButton>
      </div>
    </section>

    <!-- 争议备注弹窗 -->
    <NModal
      :show="disputeTarget !== null"
      preset="card"
      title="提出争议"
      data-test="dispute-dialog"
      @update:show="onDisputeShowChange"
    >
      <p class="meta">
        对「{{ disputeTarget ? itemTitle(disputeTarget) : '' }}」提出争议。可附言说明情况，原文证据将保留供平台人工复核。
      </p>
      <NInput
        v-model:value="disputeNote"
        type="textarea"
        :rows="3"
        :maxlength="500"
        placeholder="选填：说明争议原因"
        :input-props="disputeNoteInputProps"
      />
      <template #footer>
        <div class="modal-actions">
          <NButton data-test="dispute-cancel" @click="disputeTarget = null">取消</NButton>
          <NButton
            type="error"
            secondary
            :loading="disputeSubmitting"
            data-test="dispute-submit"
            @click="submitDispute"
          >
            提交争议
          </NButton>
        </div>
      </template>
    </NModal>
  </main>
</template>

<style scoped>
.identity-setup-view {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  min-height: 100vh;
  padding: 48px 16px;
  box-sizing: border-box;
}

.plate {
  position: relative;
  width: min(600px, 100%);
  padding: 28px 32px 32px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
}

.plate::before {
  content: '';
  position: absolute;
  inset: 6px;
  border: 1px solid var(--fg-line);
  border-radius: calc(var(--fg-radius-card) - 2px);
  pointer-events: none;
}

[data-theme='modern'] .plate::before {
  display: none;
}

.title {
  margin: 0 0 8px;
  font-family: var(--fg-font-display);
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg-ink);
}

.desc {
  margin: 0 0 20px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
}

.step {
  padding-top: 16px;
  border-top: 1px solid var(--fg-line);
}

.step-title {
  display: flex;
  align-items: center;
  margin: 0 0 8px;
  font-family: var(--fg-font-display);
  font-size: 16px;
  font-weight: 700;
  color: var(--fg-ink);
}

/* 步骤序号章：纸墨=朱砂小印；清雅=青蓝序号块（同 token 派生） */
.step-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin-right: 8px;
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-family: var(--fg-font-display);
  font-size: 13px;
  font-weight: 700;
}

.meta {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
}

.step-action {
  margin-top: 4px;
}

.error {
  margin: 0 0 12px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--fg-status-disputed);
  background-color: color-mix(in srgb, var(--fg-status-disputed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-disputed) 35%, transparent);
  border-radius: var(--fg-radius-control);
}

.empty {
  padding: 24px 0;
}

.review-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.review-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--fg-line);
}

.review-item:last-child {
  border-bottom: none;
}

.review-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.review-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.review-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--fg-ink);
}

.review-detail {
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.review-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

/* 领域状态徽章（--fg-status-*，design.md §3.4）：proposed 空心 / confirmed 实底 / disputed 虚线警示 */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  font-size: 12px;
  line-height: 1.6;
  white-space: nowrap;
  border-radius: 999px;
}

.badge-proposed {
  color: var(--fg-status-proposed);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 45%, transparent);
}

.badge-confirmed {
  color: var(--fg-surface-raised);
  background-color: var(--fg-status-confirmed);
  border: 1px solid var(--fg-status-confirmed);
}

.badge-disputed {
  color: var(--fg-status-disputed);
  background-color: color-mix(in srgb, var(--fg-status-disputed) 8%, transparent);
  border: 1px dashed var(--fg-status-disputed);
}

.done-row {
  margin-top: 20px;
}

.done-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  padding: 10px 12px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--fg-ink-secondary);
  background-color: color-mix(in srgb, var(--fg-status-confirmed) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-confirmed) 35%, transparent);
  border-radius: var(--fg-radius-control);
}

.done-hint svg {
  flex-shrink: 0;
  color: var(--fg-status-confirmed);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 768px) {
  .identity-setup-view {
    padding: 24px 12px;
  }

  .plate {
    padding: 20px 16px 24px;
  }

  .review-item {
    flex-direction: column;
    align-items: stretch;
  }

  .review-actions {
    justify-content: flex-end;
  }
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body，scoped 选择器不可达：用 data-test 锚定宽度 */
[data-test='dispute-dialog'] {
  width: min(420px, calc(100vw - 48px));
}
</style>
