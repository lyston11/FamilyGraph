<script setup lang="ts">
import { NButton } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'

import { useAgentStore } from '@/stores/agent'
import { useActionCardsStore } from '@/stores/actionCards'
import { useSpacesStore } from '@/stores/spaces'

import AgentComposer from './AgentComposer.vue'
import ErrorNotice from './ErrorNotice.vue'
import MessageList from './MessageList.vue'
import ScopeBanner from './ScopeBanner.vue'
import SessionList from './SessionList.vue'

/**
 * PanelContent：桌面抽屉与移动全屏共享的消息内容层（design.md：
 * 只有容器不同，不维护两套会话逻辑）。同时承载焦点圈闭与 Esc 关闭。
 * 顶部为助手人格标识位（design.md §2.3 Obsidian 00：Assistant 人格章，
 * 纯视觉，不承载会话行为）。
 */
const props = defineProps<{ open: boolean }>()

const emit = defineEmits<{ escape: []; close: [] }>()

const spaces = useSpacesStore()
const agent = useAgentStore()
const actionCards = useActionCardsStore()

const space = computed(() => spaces.currentSpace)
const partition = computed(() =>
  space.value !== null ? (agent.partitions.get(space.value.id) ?? null) : null,
)

const rootEl = ref<HTMLDivElement | null>(null)

function focusRoot(): void {
  rootEl.value?.focus()
}

defineExpose({ focusRoot })

// 打开时焦点入面板：onMounted 覆盖「挂载即打开」（容器分支切换会重建本组件），
// flush:'post' 的 watch 覆盖运行中的开关切换
onMounted(() => {
  if (props.open) focusRoot()
})
watch(
  () => props.open,
  (open) => {
    if (open) focusRoot()
  },
  { flush: 'post' },
)

watch(
  [() => props.open, () => space.value?.id ?? null],
  ([open, spaceId]) => {
    if (open && typeof spaceId === 'number') void actionCards.ensureLoaded(spaceId)
  },
  { immediate: true },
)

// ---- 焦点圈闭（a11y 基线：Tab 循环限制在面板内） ----

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    emit('escape')
    return
  }
  if (event.key !== 'Tab') return
  const root = rootEl.value
  if (root === null) return
  const focusable = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null,
  )
  if (focusable.length === 0) return
  const first = focusable[0] as HTMLElement
  const last = focusable[focusable.length - 1] as HTMLElement
  const active = document.activeElement
  if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

// ---- 会话与消息动作（全部委托 store）----

function onSelectSession(sessionId: number): void {
  const current = space.value
  if (current !== null) void agent.selectSession(current.id, sessionId)
}

function onCreateSession(): void {
  const current = space.value
  if (current !== null) void agent.newSession(current.id)
}

function onSend(): void {
  const current = space.value
  if (current !== null) void agent.sendMessage(current.id)
}

function onCancel(): void {
  const current = space.value
  if (current !== null) void agent.cancelRun(current.id)
}

function onRetry(): void {
  const current = space.value
  if (current !== null) void agent.reattachRun(current.id)
}

function onDraftUpdate(value: string): void {
  const current = space.value
  if (current !== null) agent.setDraft(current.id, value)
}
</script>

<template>
  <div
    ref="rootEl"
    class="panel-content"
    data-test="assistant-panel-content"
    tabindex="-1"
    role="dialog"
    :aria-hidden="!open"
    aria-label="家庭助手"
    @keydown.capture="onKeydown"
  >
    <!-- 人格标识位：助手人格章 + 名称（Steward 人格经 ActionCard 呈现，不在此处） -->
    <div class="panel-header" data-test="assistant-persona">
      <span class="persona-mark" aria-hidden="true">家</span>
      <div class="persona-text">
        <span class="persona-name">家庭助手</span>
        <span class="persona-sub">只依据已确认的档案与事实回答</span>
      </div>
      <button
        type="button"
        class="close-btn"
        aria-label="关闭家庭助手"
        data-test="assistant-panel-close"
        @click="emit('close')"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path
            fill="currentColor"
            d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"
          />
        </svg>
      </button>
    </div>

    <ScopeBanner :space="space" />
    <div class="toolbar">
      <SessionList
        v-if="partition && partition.sessions.length > 0"
        :sessions="partition.sessions"
        :active-session-id="partition.activeSessionId"
        :titles="partition.titles"
        :disabled="partition.sending"
        @select="onSelectSession"
        @create="onCreateSession"
      />
      <NButton
        v-else
        size="small"
        type="primary"
        secondary
        data-test="new-session-btn-empty"
        :disabled="!partition || partition.sending"
        @click="onCreateSession"
      >
        开始新会话
      </NButton>
    </div>

    <template v-if="partition">
      <MessageList
        :messages="partition.messages"
        :tool-summaries="partition.toolSummaries"
        :run="partition.run"
        :space-id="space?.id ?? null"
      />
      <ErrorNotice :error="partition.error" @retry="onRetry" />
      <AgentComposer
        :model-value="partition.draft"
        :sending="partition.sending"
        :can-cancel="partition.run !== null && !partition.run.terminal"
        :max-length="8000"
        @update:model-value="onDraftUpdate"
        @send="onSend"
        @cancel="onCancel"
      />
    </template>
  </div>
</template>

<style scoped>
.panel-content {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  outline: none;
  background: var(--fg-surface-raised);
}

/* 助手人格章：主色实底方章（纸墨=朱砂印 / 清雅=青蓝章），随主题 token */
.panel-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--fg-line);
}

.persona-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: var(--fg-radius-control);
  background: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-family: var(--fg-font-display);
  font-size: 17px;
  font-weight: 700;
  flex-shrink: 0;
}

.persona-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
}

.persona-name {
  color: var(--fg-ink);
  font-family: var(--fg-font-display);
  font-size: 15px;
  font-weight: 700;
  line-height: 1.3;
}

.persona-sub {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-radius: 50%;
  cursor: pointer;
  background: transparent;
  color: var(--fg-ink-secondary);
}

.close-btn:hover {
  background: var(--fg-surface-sunken);
  color: var(--fg-ink);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--fg-line);
}
</style>
