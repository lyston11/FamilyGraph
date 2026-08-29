<script setup lang="ts">
import { NAlert, NButton, NModal, useMessage } from 'naive-ui'
import { computed, ref } from 'vue'

import {
  ACTION_CARD_ERRORS,
  friendlyActionCardError,
} from '@/api/actionCards'
import { ApiError } from '@/api/errors'
import { useActionCardsStore } from '@/stores/actionCards'
import { useSpacesStore } from '@/stores/spaces'
import type { ActionCard, ActionCardState } from '@/types/actionCard'

/**
 * ActionCardItem（V2.4 Block S3，PRD ST-4）：
 * - 展示推荐原因、依据的确认事实/路径、将执行的动作、隐私影响、有效期与状态；
 * - pending/viewed → [了解详情][不接受][接受]；
 * - accepted → 两步发送：「发起申请」仅打开确认弹层（再次显示目标空间与
 *   披露影响），显式确认按钮才调用 execute——卡片接受不代替最终发送动作；
 * - 终态（executed/dismissed/expired/superseded）只读；
 * - 状态徽章阶（design.md §3.4）：沿用 .fg-badge--* 领域状态工具类 +
 *   --fg-status-* token，禁止新造颜色；过期卡整体灰化。
 */
const props = defineProps<{ card: ActionCard }>()

const actionCards = useActionCardsStore()
const spaces = useSpacesStore()
const message = useMessage()

const busy = ref(false)
const executing = ref(false)
const confirmVisible = ref(false)

// ---- 文案映射 ----

const KIND_TITLES: Record<ActionCard['kind'], string> = {
  household_link: '共建家庭空间建议',
  lineage_request: '加入族谱空间建议',
}

const STATE_LABELS: Record<ActionCardState, string> = {
  pending: '待处理',
  viewed: '已查看',
  accepted: '已接受',
  executed: '已执行',
  dismissed: '已不接受',
  expired: '已过期',
  superseded: '已被更新',
}

/** 状态 → .fg-badge--* 阶（tokens.css 领域状态工具类，两主题同源） */
const STATE_BADGE_CLASSES: Record<ActionCardState, string> = {
  pending: 'fg-badge--accent',
  viewed: 'fg-badge--proposed',
  accepted: 'fg-badge--confirmed',
  executed: 'fg-badge--confirmed',
  dismissed: 'fg-badge--neutral',
  expired: 'fg-badge--provisional',
  superseded: 'fg-badge--neutral',
}

const title = computed(() => KIND_TITLES[props.card.kind] ?? '管家建议')
const stateLabel = computed(() => STATE_LABELS[props.card.state])
const stateBadgeClass = computed(() => STATE_BADGE_CLASSES[props.card.state])

/** 过期灰化：expired 整卡降饱和（终态可辨识、不喧宾） */
const isExpired = computed(() => props.card.state === 'expired')

/** 活跃态（可操作）：pending/viewed 或 accepted（两步发送） */
const interactive = computed(
  () => props.card.state === 'pending' || props.card.state === 'viewed',
)
const canExecute = computed(() => props.card.state === 'accepted')

function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

const expiryBadge = computed(() =>
  props.card.expires_at === null ? null : `${formatDate(props.card.expires_at)} 前有效`,
)

/** 目标空间名：优先 params 投影，回退当前账号可见的空间列表，再回退 id 占位 */
const targetSpaceName = computed(() => {
  const params = props.card.proposed_action.params
  const nameFromParams = typeof params['space_name'] === 'string' ? params['space_name'] : null
  if (nameFromParams) return nameFromParams
  const spaceId =
    typeof params['space_id'] === 'number' ? params['space_id'] : props.card.space_id
  return spaces.spaces.find((s) => s.id === spaceId)?.name ?? `空间 #${spaceId}`
})

const actionText = computed(() => {
  if (props.card.proposed_action.type === 'create_household') {
    const name =
      typeof props.card.proposed_action.params['name'] === 'string'
        ? `「${props.card.proposed_action.params['name'] as string}」`
        : ''
    return `共同创建家庭空间${name}`
  }
  return `向「${targetSpaceName.value}」发送加入申请`
})

// ---- 动作 ----

function describeError(error: unknown, detailReason?: unknown): string {
  if (
    error instanceof ApiError &&
    error.code === ACTION_CARD_ERRORS.CARD_EXECUTE_REJECTED &&
    typeof detailReason === 'string' &&
    detailReason
  ) {
    return detailReason
  }
  if (error instanceof ApiError) return friendlyActionCardError(error.code, error.message)
  return '操作失败，请稍后重试'
}

async function runTransition(action: 'view' | 'dismiss' | 'accept'): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    await actionCards.transition(props.card.space_id, props.card.id, action)
  } catch (error) {
    message.warning(describeError(error))
  } finally {
    busy.value = false
  }
}

const onView = () => runTransition('view')
const onDismiss = () => runTransition('dismiss')
const onAccept = () => runTransition('accept')

/** 两步发送第二步：显式确认后才真正执行（PRD ST-5） */
async function onExecute(): Promise<void> {
  if (executing.value) return
  executing.value = true
  try {
    await actionCards.execute(props.card.space_id, props.card.id)
    confirmVisible.value = false
    message.success('申请已发送')
  } catch (error) {
    // 失败保持 accepted：弹层留在原地供用户重试或关闭
    message.error(
      describeError(
        error,
        error instanceof ApiError
          ? (error.detail as { reason?: unknown } | undefined)?.reason
          : undefined,
      ),
    )
  } finally {
    executing.value = false
  }
}
</script>

<template>
  <article
    class="action-card"
    :class="{ 'is-expired': isExpired }"
    data-test="action-card-item"
  >
    <div class="head">
      <span class="title" data-test="card-title">{{ title }}</span>
      <span class="fg-badge" :class="stateBadgeClass" data-test="card-state-tag">
        {{ stateLabel }}
      </span>
    </div>

    <p v-if="card.object_user" class="participants" data-test="card-participants">
      {{ card.subject_user.name }} ↔ {{ card.object_user.name }}
    </p>

    <!-- 为什么推荐 -->
    <p class="reason" data-test="card-reason">{{ card.reason_text }}</p>

    <!-- 依据的确认事实 / 路径 -->
    <p v-if="card.evidence.path_summary" class="evidence" data-test="card-evidence">
      依据：{{ card.evidence.path_summary }}
    </p>

    <!-- 将发生的动作 + 隐私影响 -->
    <p class="action-line" data-test="card-action">将执行：<strong>{{ actionText }}</strong></p>
    <p class="privacy" data-test="card-privacy">隐私影响：{{ card.privacy_effect }}</p>

    <div class="meta">
      <span v-if="expiryBadge" class="fg-badge fg-badge--proposed" data-test="card-expiry">
        {{ expiryBadge }}
      </span>
      <span class="created">创建于 {{ formatDate(card.created_at) }}</span>
    </div>

    <div v-if="interactive || canExecute" class="ops">
      <template v-if="interactive">
        <NButton size="small" secondary :disabled="busy" data-test="card-view-btn" @click="onView">
          了解详情
        </NButton>
        <NButton size="small" secondary :disabled="busy" data-test="card-dismiss-btn" @click="onDismiss">
          不接受
        </NButton>
        <NButton
          size="small"
          type="primary"
          :disabled="busy"
          data-test="card-accept-btn"
          @click="onAccept"
        >
          接受
        </NButton>
      </template>
      <NButton
        v-else-if="canExecute"
        size="small"
        type="primary"
        data-test="card-execute-btn"
        @click="confirmVisible = true"
      >
        发起申请
      </NButton>
    </div>

    <!-- 两步发送确认弹层：再次显示目标空间与披露影响 -->
    <NModal
      v-model:show="confirmVisible"
      preset="card"
      title="确认发起申请"
      class="execute-confirm-modal"
      data-test="execute-confirm-dialog"
    >
      <p data-test="confirm-target-space">目标空间：<strong>{{ targetSpaceName }}</strong></p>
      <p>将执行：{{ actionText }}</p>
      <NAlert type="warning" :show-icon="true" data-test="confirm-privacy">
        隐私影响：{{ card.privacy_effect }}
      </NAlert>
      <template #footer>
        <div class="modal-actions">
          <NButton data-test="execute-cancel" @click="confirmVisible = false">再想想</NButton>
          <NButton
            type="primary"
            :loading="executing"
            data-test="execute-confirm"
            @click="onExecute"
          >
            确认发送
          </NButton>
        </div>
      </template>
    </NModal>
  </article>
</template>

<style scoped>
.action-card {
  padding: 14px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface-raised);
  box-shadow: var(--fg-shadow-card);
}

/* 过期灰化（design.md §3.4）：整卡降不透明度 + 降饱和，状态章仍可读 */
.action-card.is-expired {
  opacity: 0.66;
}

.action-card.is-expired .fg-badge {
  filter: grayscale(0.5);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.title {
  font-weight: 600;
  color: var(--fg-ink);
}

.participants,
.reason,
.evidence,
.action-line,
.privacy {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.participants {
  color: var(--fg-ink);
}

.reason {
  color: var(--fg-ink);
}

.evidence,
.privacy {
  color: var(--fg-ink-secondary);
}

.action-line strong {
  color: var(--fg-ink);
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.created {
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.ops {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
