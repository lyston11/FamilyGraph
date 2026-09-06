<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import { CLIENT_AGENT_ERRORS, friendlyAgentError } from '@/api/agent'
import { useSpacesStore } from '@/stores/spaces'
import type { AgentErrorView } from '@/stores/agent'

/**
 * ErrorNotice：结构化错误码 → 中文文案映射（不透传 detail 原始 JSON）。
 * STREAM_LOST 时提供「重试」入口恢复当前 Run 订阅；错误携带
 * open-model-settings 动作且当前空间可管理时（R4），提供「去模型设置」
 * 直达 /spaces/{id}/manage?section=models，非管理员保持纯文案。
 */
const props = defineProps<{ error: AgentErrorView | null }>()

const emit = defineEmits<{ retry: [] }>()

const router = useRouter()
const spaces = useSpacesStore()

const text = computed(() => {
  if (props.error === null) return ''
  if (props.error.message) return props.error.message
  return friendlyAgentError(props.error.code)
})

const canRetry = computed(() => props.error?.code === CLIENT_AGENT_ERRORS.STREAM_LOST)

// 与 AppShell 同口径：仅当前空间的 active space_admin 才显示管理直达入口
const canManageCurrentSpace = computed(() => spaces.canManageSpace && spaces.currentSpace !== null)

const showModelSettingsJump = computed(
  () => props.error?.action?.kind === 'open-model-settings' && canManageCurrentSpace.value,
)

function goModelSettings(): void {
  const space = spaces.currentSpace
  if (space === null) return
  void router.push({
    name: 'space-management',
    params: { spaceId: space.id },
    query: { section: 'models' },
  })
}
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
      v-if="showModelSettingsJump"
      type="button"
      class="action-link"
      data-test="error-open-model-settings"
      @click="goModelSettings"
    >
      去模型设置
    </button>
    <button
      v-if="canRetry"
      type="button"
      class="action-link"
      data-test="error-retry"
      @click="emit('retry')"
    >
      重试
    </button>
  </div>
</template>

<style scoped>
/* 争议/错误语义走 --fg-status-disputed（design.md §3.4），柔底用 color-mix 派生 */
.error-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 14px 8px;
  padding: 8px 12px;
  border-radius: var(--fg-radius-control);
  background: color-mix(in srgb, var(--fg-status-disputed) 8%, var(--fg-surface-raised));
  color: var(--fg-status-disputed);
  font-size: 13px;
}

.icon {
  flex-shrink: 0;
}

.text {
  flex: 1;
  min-width: 0;
}

.action-link {
  flex-shrink: 0;
  border: none;
  background: none;
  color: var(--fg-status-disputed);
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
}

.action-link:focus-visible {
  outline: 2px solid var(--fg-status-disputed);
  outline-offset: 2px;
}
</style>
