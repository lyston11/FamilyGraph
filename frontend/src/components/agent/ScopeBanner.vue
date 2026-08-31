<script setup lang="ts">
import { computed } from 'vue'

import type { FamilySpace } from '@/types/api'

/**
 * ScopeBanner（PRD AS-3）：空间名称与 kind 徽标始终可见，
 * 发送前能确认「正在询问哪个空间」。
 */
const props = defineProps<{ space: FamilySpace | null }>()

const kindLabel = computed(() => {
  if (props.space?.kind === 'lineage') return '宗族空间'
  if (props.space?.kind === 'household') return '家庭空间'
  return ''
})
</script>

<template>
  <div class="scope-banner" data-test="scope-banner" role="status">
    <span class="dot" aria-hidden="true"></span>
    <span v-if="space" class="scope-text">
      正在询问：<strong>{{ space.name }}</strong>
      <span class="fg-badge fg-badge--accent kind-badge">{{ kindLabel }}</span>
    </span>
    <span v-else class="scope-text muted">暂无可用空间</span>
  </div>
</template>

<style scoped>
.scope-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  background: var(--fg-surface-sunken);
  border-bottom: 1px solid var(--fg-line);
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--fg-status-confirmed);
  flex-shrink: 0;
}

.scope-text strong {
  color: var(--fg-ink);
}

.kind-badge {
  margin-left: 6px;
}

.muted {
  color: var(--fg-ink-secondary);
}
</style>
