<script setup lang="ts">
// 通知行展示（PRD §2.6）：未读标记、kind 标签、标题、领域状态标签、
// 脱敏摘要/相关人/空间名（MaskedField）、时间。纯展示组件：
// 不做任何状态变更，点击行为由宿主列表项承担。
import { computed } from 'vue'

import MaskedField from '@/components/common/MaskedField.vue'
import type { NotificationItem } from '@/types/api'
import {
  NOTIFICATION_DOMAIN_STATUS_BADGES,
  NOTIFICATION_DOMAIN_STATUS_LABELS,
  NOTIFICATION_KIND_LABELS,
} from '@/types/notifications'

const props = defineProps<{ item: NotificationItem }>()

// masked 联合类型的判别在 script 内完成（unknown 交给 MaskedField 单点渲染）
const summary = computed<{ masked: boolean; text: string | null }>(() => {
  const value = props.item.payload.summary
  if (value === null) return { masked: false, text: null }
  if (typeof value === 'object') return { masked: true, text: null }
  return { masked: false, text: value }
})

const actorName = computed<{ masked: boolean; text: string | null }>(() => {
  const value: string | { __masked__: true } | null = props.item.payload.actor_name
  if (value === null) return { masked: false, text: null }
  if (typeof value === 'object') return { masked: true, text: null }
  return { masked: false, text: value }
})

const spaceName = computed<{ masked: boolean; text: string | null }>(() => {
  const value: string | { __masked__: true } | null = props.item.payload.space_name
  if (value === null) return { masked: false, text: null }
  if (typeof value === 'object') return { masked: true, text: null }
  return { masked: false, text: value }
})

function formatTime(iso: string): string {
  return iso.replace('T', ' ').slice(0, 16)
}
</script>

<template>
  <div class="notice-row">
    <div class="notice-main">
      <div class="notice-topline">
        <span
          v-if="item.read_at === null"
          class="fg-badge fg-badge--accent"
          data-test="unread-badge"
        >
          <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
            <circle cx="12" cy="12" r="5" />
          </svg>
          未读
        </span>
        <span class="fg-badge fg-badge--neutral">{{ NOTIFICATION_KIND_LABELS[item.kind] }}</span>
        <strong class="notice-title">{{ item.payload.title }}</strong>
        <!-- 领域状态标签：严格独立于已读状态（已读不改变领域状态） -->
        <span
          class="fg-badge"
          :class="NOTIFICATION_DOMAIN_STATUS_BADGES[item.domain_status]"
          data-test="domain-status"
        >
          {{ NOTIFICATION_DOMAIN_STATUS_LABELS[item.domain_status] }}
        </span>
      </div>
      <p v-if="summary.text !== null || summary.masked" class="notice-summary">
        摘要：<MaskedField v-if="summary.masked" :value="item.payload.summary" />
        <template v-else>{{ summary.text }}</template>
      </p>
      <div class="notice-meta">
        <span v-if="actorName.text !== null || actorName.masked" class="notice-meta-item">
          相关人：<MaskedField v-if="actorName.masked" :value="item.payload.actor_name" />
          <template v-else>{{ actorName.text }}</template>
        </span>
        <span v-if="spaceName.text !== null || spaceName.masked" class="notice-meta-item">
          空间：<MaskedField v-if="spaceName.masked" :value="item.payload.space_name" />
          <template v-else>{{ spaceName.text }}</template>
        </span>
        <span>{{ formatTime(item.created_at) }}</span>
      </div>
    </div>
    <div v-if="$slots.actions" class="notice-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.notice-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.notice-main {
  min-width: 0;
  flex: 1;
}

.notice-topline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}

.notice-topline .fg-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.notice-title {
  color: var(--fg-ink);
  font-size: 14px;
}

.notice-summary {
  margin: 6px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.notice-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 6px;
  color: var(--fg-ink-faint);
  font-size: 11px;
}

.notice-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.notice-actions {
  flex-shrink: 0;
  align-self: center;
}

@media (max-width: 600px) {
  .notice-row {
    flex-direction: column;
    gap: 8px;
  }

  .notice-actions {
    align-self: flex-start;
  }
}
</style>
