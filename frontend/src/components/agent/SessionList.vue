<script setup lang="ts">
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

const options = computed(() =>
  props.sessions.map((session) => ({
    id: session.id,
    title: props.titles[session.id] || formatFallbackTitle(session),
  })),
)
</script>

<template>
  <div class="session-list" data-test="session-list">
    <el-select
      :model-value="activeSessionId"
      class="select"
      size="small"
      aria-label="选择会话"
      data-test="session-select"
      :disabled="disabled"
      @update:model-value="(value: number | undefined) => value !== undefined && emit('select', value)"
    >
      <el-option v-for="opt in options" :key="opt.id" :value="opt.id" :label="opt.title" />
    </el-select>
    <el-button
      size="small"
      data-test="new-session-btn"
      :disabled="disabled"
      @click="emit('create')"
    >
      新会话
    </el-button>
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
