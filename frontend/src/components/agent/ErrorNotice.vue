<script setup lang="ts">
import { computed } from 'vue'

import { CLIENT_AGENT_ERRORS, friendlyAgentError } from '@/api/agent'

/**
 * ErrorNotice：结构化错误码 → 中文文案映射（不透传 detail 原始 JSON）。
 * STREAM_LOST 时提供「重试」入口恢复当前 Run 订阅。
 */
const props = defineProps<{ error: { code: string; message: string } | null }>()

const emit = defineEmits<{ retry: [] }>()

const text = computed(() => {
  if (props.error === null) return ''
  if (props.error.message) return props.error.message
  return friendlyAgentError(props.error.code)
})

const canRetry = computed(() => props.error?.code === CLIENT_AGENT_ERRORS.STREAM_LOST)
</script>

<template>
  <div
    v-if="error !== null"
    class="error-notice"
    data-test="error-notice"
    role="alert"
  >
    <svg class="icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 2 1 21h22L12 2zm1 14h-2v2h2v-2zm0-7h-2v5h2V9z"
      />
    </svg>
    <span class="text">{{ text }}</span>
    <button
      v-if="canRetry"
      type="button"
      class="retry"
      data-test="error-retry"
      @click="emit('retry')"
    >
      重试
    </button>
  </div>
</template>

<style scoped>
.error-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 14px 8px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 13px;
}

.icon {
  flex-shrink: 0;
}

.text {
  flex: 1;
  min-width: 0;
}

.retry {
  border: none;
  background: none;
  color: var(--el-color-danger);
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
}

.retry:focus-visible {
  outline: 2px solid var(--el-color-danger);
  outline-offset: 2px;
}
</style>
