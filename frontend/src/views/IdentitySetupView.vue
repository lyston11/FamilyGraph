<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useGovernanceStore } from '@/stores/governance'
import type { FactReview } from '@/types/api'

/**
 * 首登确档向导（v2 F-1）：「这是我」合并确认 → 确档清单逐项确认/争议。
 *
 * - 第一步是 Account managed→claimed 与本人 Profile provisional→identity_confirmed
 *   的唯一合法联动（POST /me/identity/confirm）；已确认过则后端 409，视为通过。
 * - 第二步逐项决议 profile_fact_reviews：confirmed | disputed（终态）。
 * - 全部决议完成后放行进入主界面；路由守卫依据 governance store 判断。
 */
const auth = useAuthStore()
const governance = useGovernanceStore()
const router = useRouter()
const route = useRoute()

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

async function submitDispute(): Promise<void> {
  const target = disputeTarget.value
  if (!target) return
  disputeSubmitting.value = true
  try {
    await governance.decideReviewItem(target.id, 'disputed', disputeNote.value.trim() || null)
    disputeTarget.value = null
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    disputeSubmitting.value = false
  }
}

async function confirmReview(review: FactReview): Promise<void> {
  try {
    await governance.decideReviewItem(review.id, 'confirmed')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  }
}

function finish(): void {
  const target = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
  void router.replace(target)
}
</script>

<template>
  <main class="identity-setup-view">
    <el-card class="card" data-test="setup-card">
      <h1 class="title">完善你的身份信息</h1>
      <p class="desc">首次登录需要确认档案归属，并核对家人为你填写的资料。</p>

      <!-- 第一步：这是我 -->
      <section v-if="!identityConfirmed" class="step" data-test="confirm-step">
        <h2 class="step-title">1 · 这是我的档案</h2>
        <p class="meta">
          档案「{{ auth.user?.name }}」当前由家人代管。确认后账号归你所有，初始 PIN 将保持已更换状态。
        </p>
        <p v-if="errorMessage" class="error" data-test="confirm-error">{{ errorMessage }}</p>
        <el-button
          type="primary"
          :loading="confirming"
          data-test="confirm-btn"
          @click="doConfirmIdentity"
        >
          这是我，确认归属
        </el-button>
      </section>

      <!-- 第二步：确档清单 -->
      <section v-else class="step" data-test="checklist-step">
        <h2 class="step-title">2 · 核对资料清单</h2>
        <p class="meta">以下资料由建档人提供，逐条确认或提出争议。有争议的条目将保留原文供平台复核。</p>

        <div v-loading="loadingReviews">
          <el-empty
            v-if="!loadingReviews && governance.factReviews.length === 0"
            description="没有待核对的资料"
            :image-size="60"
          />
          <ul v-else class="review-list" data-test="review-list">
            <li
              v-for="review in governance.factReviews"
              :key="review.id"
              class="review-item"
              :data-test="`review-item-${review.id}`"
            >
              <div class="review-main">
                <span class="review-title">{{ itemTitle(review) }}</span>
                <span class="review-detail">{{ itemDetail(review) }}</span>
              </div>
              <div class="review-actions">
                <template v-if="review.status === 'proposed'">
                  <el-button
                    size="small"
                    type="primary"
                    plain
                    :data-test="`review-confirm-${review.id}`"
                    @click="confirmReview(review)"
                  >
                    确认无误
                  </el-button>
                  <el-button
                    size="small"
                    type="warning"
                    plain
                    :data-test="`review-dispute-${review.id}`"
                    @click="askDispute(review)"
                  >
                    有争议
                  </el-button>
                </template>
                <el-tag v-else-if="review.status === 'confirmed'" size="small" type="success">
                  已确认
                </el-tag>
                <el-tag v-else size="small" type="warning">已提出争议</el-tag>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <div v-if="canFinish" class="done-row">
        <el-alert type="success" :closable="false" data-test="done-hint">
          资料核对完成！你已具备完整的身份状态。
        </el-alert>
        <el-button type="primary" data-test="finish-btn" @click="finish">进入 FamilyGraph</el-button>
      </div>
    </el-card>

    <!-- 争议备注弹窗 -->
    <el-dialog
      :model-value="disputeTarget !== null"
      title="提出争议"
      width="380px"
      append-to-body
      data-test="dispute-dialog"
      @update:model-value="disputeTarget = null"
    >
      <p class="meta">
        对「{{ disputeTarget ? itemTitle(disputeTarget) : '' }}」提出争议。可附言说明情况，原文证据将保留供平台人工复核。
      </p>
      <el-input
        v-model="disputeNote"
        type="textarea"
        :rows="3"
        maxlength="500"
        placeholder="选填：说明争议原因"
        data-test="dispute-note-input"
      />
      <template #footer>
        <el-button data-test="dispute-cancel" @click="disputeTarget = null">取消</el-button>
        <el-button
          type="warning"
          :loading="disputeSubmitting"
          data-test="dispute-submit"
          @click="submitDispute"
        >
          提交争议
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.identity-setup-view {
  display: flex;
  justify-content: center;
  align-items: flex-start;
  min-height: 100vh;
  padding: 48px 16px;
}

.card {
  width: 560px;
}

.title {
  margin: 0 0 8px;
  font-size: 20px;
}

.desc {
  color: var(--el-text-color-secondary);
  margin-top: 0;
}

.step {
  margin-top: 16px;
}

.step-title {
  margin: 0 0 8px;
  font-size: 15px;
}

.meta {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.review-list {
  list-style: none;
  margin: 12px 0;
  padding: 0;
}

.review-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.review-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.review-title {
  font-weight: 600;
  font-size: 14px;
}

.review-detail {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.review-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.done-row {
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.error {
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
