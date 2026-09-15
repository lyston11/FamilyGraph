<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import ActionCardItem from '@/components/actioncard/ActionCardItem.vue'
import CitationList from '@/components/memory/CitationList.vue'
import WebCitationList from '@/components/agent/WebCitationList.vue'
import { useActionCardsStore } from '@/stores/actionCards'
import { useAgentStore, type ActiveRunView, type AgentMessageView, type ToolSummaryView } from '@/stores/agent'

/**
 * MessageList（PRD AS-3）：文本气泡、进行中状态、工具使用摘要 chip。
 * 只渲染白名单投影字段；不展示内部 prompt、tool schema、原始 payload。
 * 流式新增经 aria-live=polite 非打断播报。
 * 视觉（design.md §2.3）：助手气泡=纸面浅沉底，用户气泡=主色实底（SSE
 * 流式气泡随主题 token，分片到达时逐步渲染）。
 */
const props = defineProps<{
  messages: AgentMessageView[]
  toolSummaries: ToolSummaryView[]
  run: ActiveRunView | null
  /** Assistant 消息引用卡片时使用的当前空间；其它场景可不传。 */
  spaceId?: number | null
}>()

const actionCards = useActionCardsStore()
const agent = useAgentStore()

const runActive = computed(() => props.run !== null && !props.run.terminal)

// ---- 09-13-agent-latency-tuning：长时间 queued/running 的用户可见提示 ----
// queued 超过阈值指向执行器（sidecar）离线等运维问题；推理中超阈值说明仍在生成。
const QUEUED_HINT_AFTER_SECONDS = 10
const RUNNING_HINT_AFTER_SECONDS = 30

const nowMs = ref(Date.now())
let tickHandle: ReturnType<typeof setInterval> | null = null
const runStartedAtMs = ref<number | null>(null)

watch(
  () => props.run?.id ?? null,
  (id) => {
    runStartedAtMs.value = id === null ? null : Date.now()
  },
  { immediate: true },
)

watch(
  runActive,
  (active) => {
    if (active && tickHandle === null) {
      tickHandle = setInterval(() => {
        nowMs.value = Date.now()
      }, 1000)
    } else if (!active && tickHandle !== null) {
      clearInterval(tickHandle)
      tickHandle = null
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (tickHandle !== null) {
    clearInterval(tickHandle)
    tickHandle = null
  }
})

const activeSeconds = computed(() => {
  if (!runActive.value || runStartedAtMs.value === null) return 0
  return Math.max(0, Math.floor((nowMs.value - runStartedAtMs.value) / 1000))
})

const runHint = computed(() => {
  if (!runActive.value) return ''
  if (props.run?.status === 'queued' && activeSeconds.value >= QUEUED_HINT_AFTER_SECONDS) {
    return `仍在排队（已 ${activeSeconds.value} 秒）。长时间排队通常是服务端执行器离线，请联系系统管理员。`
  }
  if (props.run?.status !== 'queued' && activeSeconds.value >= RUNNING_HINT_AFTER_SECONDS) {
    return `回复生成需要较长时间（已 ${activeSeconds.value} 秒），仍在进行中。`
  }
  return ''
})

/** 等待首个有正文的助手回复时显示进行中指示（工具 turn 的空消息不熄灭它） */
const showPendingIndicator = computed(
  () => runActive.value && !props.messages.some((m) => m.role === 'assistant' && m.text.length > 0),
)

/** 屏幕阅读器非打断播报：最新动态一句话 */
const announcement = computed(() => {
  const last = props.messages[props.messages.length - 1]
  if (showPendingIndicator.value) return runHint.value || '助手正在思考'
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

const items = computed(() =>
  props.messages.map((message, index) => {
    const spaceId = props.spaceId
    const cards =
      message.role === 'assistant' && typeof spaceId === 'number'
        ? (message.cardIds ?? [])
            .map((cardId) => actionCards.cardsOf(spaceId).find((card) => card.id === cardId))
            .filter((card): card is NonNullable<typeof card> => card !== undefined)
        : []
    return {
      ...message,
      original: message,
      cards,
      citations: message.citations ?? [],
      unavailable: message.unavailableCitationCount ?? 0,
      webCitations: message.webCitations ?? [],
      key: `${index}-${message.id ?? 'local'}`,
    }
  }),
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
        <div v-if="item.citations.length > 0" class="message-citations" data-test="message-citations">
          <CitationList :citations="item.citations" compact />
        </div>
        <p
          v-if="item.unavailable > 0"
          class="message-citations-unavailable"
          data-test="message-citations-unavailable"
        >
          部分来源已不可用（{{ item.unavailable }}）
        </p>
        <p v-if="item.citationLoadState === 'loading'" class="message-citations-unavailable" role="status">
          正在加载来源…
        </p>
        <p v-else-if="item.citationLoadState === 'failed'" class="message-citations-unavailable" data-test="citation-load-failed">
          来源加载失败。
          <button type="button" @click="agent.retryMessageCitations(item.original)">重新加载来源</button>
        </p>
        <div v-if="item.webCitations.length > 0" class="message-citations" data-test="message-web-citations">
          <WebCitationList :citations="item.webCitations" compact />
        </div>
        <div v-if="item.cards.length > 0" class="message-cards" data-test="message-cards">
          <ActionCardItem v-for="card in item.cards" :key="card.id" :card="card" />
        </div>
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

    <!-- 长时间 queued/running 的原因提示（09-13-agent-latency-tuning AC-4） -->
    <p v-if="runHint" class="run-hint" data-test="run-hint">{{ runHint }}</p>

    <!-- 非打断 live region -->
    <div class="sr-only" aria-live="polite" data-test="live-region">{{ announcement }}</div>
  </div>
</template>

<style scoped>
.message-citations-unavailable {
  margin: 4px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty {
  color: var(--fg-ink-secondary);
  font-size: 13px;
  text-align: center;
  margin-top: 32px;
}

.bubble-row {
  display: flex;
}

.bubble-row.assistant {
  flex-direction: column;
  align-items: flex-start;
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

/* 助手=纸面浅沉底 + 发丝线（纸墨如便笺，清雅如浅灰气泡）；用户=主色实底 */
.bubble-row.assistant .bubble {
  background: var(--fg-surface-sunken);
  color: var(--fg-ink);
  border: 1px solid var(--fg-line);
}

.bubble-row.user .bubble {
  background: var(--fg-accent);
  color: var(--fg-accent-ink);
}

.bubble.failed {
  background: color-mix(in srgb, var(--fg-status-disputed) 8%, var(--fg-surface-raised));
  color: var(--fg-status-disputed);
}

.message-cards {
  width: min(100%, 420px);
  margin-top: 6px;
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
  background: var(--fg-surface-sunken);
  color: var(--fg-ink-secondary);
  border: 1px solid var(--fg-line);
}

.tool-chip em {
  font-style: normal;
  color: var(--fg-ink-faint);
}

.tool-chip.ok em {
  color: var(--fg-status-confirmed);
}

.tool-chip.error em {
  color: var(--fg-status-disputed);
}

.thinking {
  display: flex;
  gap: 4px;
  padding: 4px 2px;
}

.run-hint {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
  padding: 0 2px;
}

.thinking span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg-ink-faint);
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
