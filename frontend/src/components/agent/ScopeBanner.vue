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
      <span class="kind-badge">{{ kindLabel }}</span>
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
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 13px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--el-color-success);
  flex-shrink: 0;
}

.kind-badge {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 3px;
  font-size: 12px;
  line-height: 18px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.muted {
  color: var(--el-text-color-secondary);
}
</style>
