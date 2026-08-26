<script setup lang="ts">
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
 */
const props = defineProps<{ open: boolean }>()

const emit = defineEmits<{ escape: [] }>()

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
      <el-button
        v-else
        size="small"
        type="primary"
        plain
        data-test="new-session-btn-empty"
        :disabled="!partition || partition.sending"
        @click="onCreateSession"
      >
        开始新会话
      </el-button>
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
  background: var(--el-bg-color);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
</style>
