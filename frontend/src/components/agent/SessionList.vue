<script setup lang="ts">
import { NButton, NSelect } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed } from 'vue'

import type { AgentSession } from '@/types/agent'

/**
 * SessionList：当前空间的会话切换与新建（PRD AS-1：一个空间可有多个 Session）。
 * 标题 = 首条用户消息截断 24 字（纯展示，不成为记忆或事实）；无历史时回退创建时间。
 */
const props = defineProps<{
  sessions: AgentSession[]
  activeSessionId: number | null
  titles: Record<number, string>
  disabled?: boolean
}>()

const emit = defineEmits<{ select: [sessionId: number]; create: [] }>()

function formatFallbackTitle(session: AgentSession): string {
  const date = new Date(session.created_at)
  if (Number.isNaN(date.getTime())) return `会话 #${session.id}`
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const options = computed<SelectOption[]>(() =>
  props.sessions.map((session) => ({
    value: session.id,
    label: props.titles[session.id] || formatFallbackTitle(session),
  })),
)

function onSelect(value: number | string | null): void {
  if (typeof value === 'number') emit('select', value)
}
</script>

<template>
  <div class="session-list" data-test="session-list">
    <!-- aria-label 落包裹层（naive-ui 不透传到原生 input，P6 记入规范约定） -->
    <NSelect
      class="select"
      size="small"
      :value="activeSessionId"
      :options="options"
      :disabled="disabled"
      data-test="session-select"
      aria-label="选择会话"
      @update:value="onSelect"
    />
    <NButton
      size="small"
      secondary
      data-test="new-session-btn"
      :disabled="disabled"
      @click="emit('create')"
    >
      新会话
    </NButton>
  </div>
</template>

<style scoped>
.session-list {
  display: flex;
  gap: 8px;
  align-items: center;
}

.select {
  flex: 1;
  min-width: 0;
}
</style>
