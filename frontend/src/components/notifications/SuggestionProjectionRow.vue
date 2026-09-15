<script setup lang="ts">
// 建议投影行展示：通知中心「待核实」分区里直接渲染服务端授权的活跃建议。
//
// 与 NoticeItemRow 的区别：本组件渲染的是 StewardSuggestion 投影本身，
// 而不是通知记录（term_preference 刻意不产生通知，只有投影）。纯展示组件：
// 不做任何状态变更，不标记已读，点击行为由宿主列表项承担。
import { computed } from 'vue'

import type { SuggestionItem } from '@/types/api'
import { SUGGESTION_KIND_LABELS, SUGGESTION_STATE_LABELS } from '@/types/notifications'

const props = defineProps<{ item: SuggestionItem }>()

const subjectText = computed(
  () => props.item.subject_name ?? `用户 ${props.item.subject_user_id}`,
)
const objectText = computed(() =>
  props.item.object_user_id === null
    ? null
    : props.item.object_name ?? `用户 ${props.item.object_user_id}`,
)

/**
 * 行内建议值：
 * - term_preference 的 presentation.summary 是通用文案（「可选的称谓偏好建议，无需处理」），
 *   真正要展示的叫法在 value.term（与 KinshipTermPanel 同口径）；
 * - relation_proposal 用服务端方向化的 presentation.summary。
 */
const valueText = computed<string | null>(() => {
  if (props.item.kind === 'term_preference') {
    const term = props.item.value.term
    return typeof term === 'string' && term !== '' ? term : null
  }
  return props.item.presentation?.summary ?? null
})

const valueLabel = computed(() =>
  props.item.kind === 'term_preference' ? '建议叫法' : '关系线索',
)
</script>

<template>
  <div class="sug-row">
    <div class="sug-main">
      <div class="sug-topline">
        <span class="fg-badge fg-badge--neutral">{{ SUGGESTION_KIND_LABELS[item.kind] }}</span>
        <strong class="sug-title">
          {{ subjectText }}<template v-if="objectText !== null"> · {{ objectText }}</template>
        </strong>
        <span class="fg-badge fg-badge--accent" data-test="suggestion-state">
          {{ SUGGESTION_STATE_LABELS[item.state] }}
        </span>
      </div>
      <p v-if="valueText !== null" class="sug-value">{{ valueLabel }}：{{ valueText }}</p>
    </div>
    <div v-if="$slots.actions" class="sug-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.sug-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.sug-main {
  min-width: 0;
  flex: 1;
}

.sug-topline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}

.sug-topline .fg-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.sug-title {
  color: var(--fg-ink);
  font-size: 14px;
}

.sug-value {
  margin: 6px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.sug-actions {
  flex-shrink: 0;
  align-self: center;
}

@media (max-width: 600px) {
  .sug-row {
    flex-direction: column;
    gap: 8px;
  }

  .sug-actions {
    align-self: flex-start;
  }

  .sug-actions :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
