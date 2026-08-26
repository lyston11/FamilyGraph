<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

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
 * - 终态（executed/dismissed/expired/superseded）只读。
 */
const props = defineProps<{ card: ActionCard }>()

const actionCards = useActionCardsStore()
const spaces = useSpacesStore()

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

const STATE_TAG_TYPES: Record<ActionCardState, 'primary' | 'success' | 'warning' | 'info'> = {
  pending: 'warning',
  viewed: 'primary',
  accepted: 'success',
  executed: 'success',
  dismissed: 'info',
  expired: 'info',
  superseded: 'info',
}

const title = computed(() => KIND_TITLES[props.card.kind] ?? '管家建议')
const stateLabel = computed(() => STATE_LABELS[props.card.state])
const stateTagType = computed(() => STATE_TAG_TYPES[props.card.state])

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
    ElMessage.warning(describeError(error))
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
    ElMessage.success('申请已发送')
  } catch (error) {
    // 失败保持 accepted：弹层留在原地供用户重试或关闭
    ElMessage.error(
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
  <el-card class="action-card" shadow="never" data-test="action-card-item">
    <div class="head">
      <span class="title" data-test="card-title">{{ title }}</span>
      <el-tag size="small" :type="stateTagType" data-test="card-state-tag">{{ stateLabel }}</el-tag>
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
      <el-tag v-if="expiryBadge" size="small" type="warning" effect="plain" data-test="card-expiry">
        {{ expiryBadge }}
      </el-tag>
      <span class="created">创建于 {{ formatDate(card.created_at) }}</span>
    </div>

    <div v-if="interactive || canExecute" class="ops">
      <template v-if="interactive">
        <el-button size="small" :disabled="busy" data-test="card-view-btn" @click="onView">
          了解详情
        </el-button>
        <el-button size="small" :disabled="busy" data-test="card-dismiss-btn" @click="onDismiss">
          不接受
        </el-button>
        <el-button
          size="small"
          type="primary"
          :disabled="busy"
          data-test="card-accept-btn"
          @click="onAccept"
        >
          接受
        </el-button>
      </template>
      <el-button
        v-else-if="canExecute"
        size="small"
        type="primary"
        data-test="card-execute-btn"
        @click="confirmVisible = true"
      >
        发起申请
      </el-button>
    </div>

    <!-- 两步发送确认弹层：再次显示目标空间与披露影响 -->
    <el-dialog
      v-model="confirmVisible"
      title="确认发起申请"
      width="420px"
      append-to-body
      data-test="execute-confirm-dialog"
      @update:model-value="(value: boolean) => (confirmVisible = value)"
    >
      <p data-test="confirm-target-space">目标空间：<strong>{{ targetSpaceName }}</strong></p>
      <p>将执行：{{ actionText }}</p>
      <el-alert type="warning" :closable="false" data-test="confirm-privacy">
        隐私影响：{{ card.privacy_effect }}
      </el-alert>
      <template #footer>
        <el-button data-test="execute-cancel" @click="confirmVisible = false">再想想</el-button>
        <el-button
          type="primary"
          :loading="executing"
          data-test="execute-confirm"
          @click="onExecute"
        >
          确认发送
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.action-card {
  border: 1px solid var(--el-border-color-lighter);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.title {
  font-weight: 600;
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

.reason {
  color: var(--el-text-color-primary);
}

.evidence,
.privacy {
  color: var(--el-text-color-secondary);
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.created {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.ops {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 10px;
}
</style>
