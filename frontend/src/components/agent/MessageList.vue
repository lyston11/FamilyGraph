<script setup lang="ts">
import { computed } from 'vue'

import type { ActiveRunView, AgentMessageView, ToolSummaryView } from '@/stores/agent'

/**
 * MessageList（PRD AS-3）：文本气泡、进行中状态、工具使用摘要 chip。
 * 只渲染白名单投影字段；不展示内部 prompt、tool schema、原始 payload。
 * 流式新增经 aria-live=polite 非打断播报。
 */
const props = defineProps<{
  messages: AgentMessageView[]
  toolSummaries: ToolSummaryView[]
  run: ActiveRunView | null
}>()

const runActive = computed(() => props.run !== null && !props.run.terminal)

/** 等待首个助手回复时显示进行中指示 */
const showPendingIndicator = computed(
  () => runActive.value && !props.messages.some((m) => m.role === 'assistant'),
)

/** 屏幕阅读器非打断播报：最新动态一句话 */
const announcement = computed(() => {
  const last = props.messages[props.messages.length - 1]
  if (showPendingIndicator.value) return '助手正在思考'
  if (last && last.role === 'assistant') {
    const text = Array.from(last.text)
    return text.length > 50 ? `助手回复：${text.slice(0, 50).join('')}…` : `助手回复：${last.text}`
  }
  return ''
})

function toolStatusText(status: ToolSummaryView['status']): string {
  return status === 'running' ? '执行中' : status === 'ok' ? '成功' : '失败'
}

function roleLabel(role: AgentMessageView['role']): string {
  return role === 'user' ? '我' : '助手'
}

/** 预生成稳定 key（规避 vue-tsc 对 template v-for index 的误报） */
const items = computed(() =>
  props.messages.map((message, index) => ({
    ...message,
    key: `${index}-${message.id ?? 'local'}`,
  })),
)
</script>

<template>
  <div class="message-list" data-test="message-list">
    <p v-if="messages.length === 0 && !runActive" class="empty">
      试试问：「这个空间里谁是我的长辈？」
    </p>

    <template v-for="item in items" :key="item.key">
      <div
        class="bubble-row"
        :class="item.role"
        data-test="message-item"
        :data-role="item.role"
      >
        <div class="bubble" :class="{ failed: item.status === 'failed' }">
          {{ item.text }}
          <span v-if="item.status === 'pending'" class="pending-mark">发送中…</span>
          <span v-else-if="item.status === 'failed'" class="failed-mark">发送失败</span>
        </div>
        <span class="sr-only">{{ roleLabel(item.role) }}说</span>
      </div>
    </template>

    <!-- 工具使用摘要：图标 + tool_name + 成功/失败，不含原始 payload -->
    <div v-if="toolSummaries.length > 0" class="tool-chips" data-test="tool-chips">
      <span
        v-for="summary in toolSummaries"
        :key="summary.toolCallId"
        class="tool-chip"
        :class="summary.status"
        data-test="tool-chip"
      >
        <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
          <path
            fill="currentColor"
            d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4z"
          />
        </svg>
        {{ summary.toolName }}
        <em>{{ toolStatusText(summary.status) }}</em>
      </span>
    </div>

    <div v-if="showPendingIndicator" class="thinking" data-test="thinking-indicator" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>

    <!-- 非打断 live region -->
    <div class="sr-only" aria-live="polite" data-test="live-region">{{ announcement }}</div>
  </div>
</template>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  text-align: center;
  margin-top: 32px;
}

.bubble-row {
  display: flex;
}

.bubble-row.user {
  justify-content: flex-end;
}

.bubble {
  max-width: 82%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble-row.assistant .bubble {
  background: var(--el-fill-color-light);
}

.bubble-row.user .bubble {
  background: var(--el-color-primary);
  color: #fff;
}

.bubble.failed {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.pending-mark,
.failed-mark {
  display: inline-block;
  margin-left: 6px;
  font-size: 12px;
  opacity: 0.85;
}

.tool-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
  border: 1px solid var(--el-border-color-lighter);
}

.tool-chip em {
  font-style: normal;
  color: var(--el-text-color-secondary);
}

.tool-chip.ok em {
  color: var(--el-color-success);
}

.tool-chip.error em {
  color: var(--el-color-danger);
}

.thinking {
  display: flex;
  gap: 4px;
  padding: 4px 2px;
}

.thinking span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-text-color-secondary);
  animation: blink 1.2s infinite ease-in-out;
}

.thinking span:nth-child(2) {
  animation-delay: 0.2s;
}

.thinking span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

/* reduced motion：去掉动画只保留静态指示 */
@media (prefers-reduced-motion: reduce) {
  .thinking span {
    animation: none;
    opacity: 0.6;
  }
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
</style>
