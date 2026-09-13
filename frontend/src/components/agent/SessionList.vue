<script setup lang="ts">
import { NButton } from 'naive-ui'
import { computed, onBeforeUnmount, ref } from 'vue'

import type { AgentSession } from '@/types/agent'

/**
 * SessionList：当前空间的会话切换、新建、重命名与删除（PRD AS-1 / R3）。
 * 标题解析顺序：服务端 title → 内存 titles（乐观首条消息）→ 创建时间回退格式。
 * 列表为面板内文档流展开（不用浮层）：折叠态显示当前会话标题，展开态为
 * 会话条目（标题 + 相对时间 + 当前会话高亮 + 行内重命名/两步确认删除）。
 */
const props = defineProps<{
  sessions: AgentSession[]
  activeSessionId: number | null
  titles: Record<number, string>
  disabled?: boolean
}>()

const emit = defineEmits<{
  select: [sessionId: number]
  create: []
  rename: [sessionId: number, title: string]
  delete: [sessionId: number]
}>()

const SESSION_TITLE_MAX_LENGTH = 120
const DELETE_CONFIRM_RESET_MS = 4000

const expanded = ref(false)
const renamingId = ref<number | null>(null)
const renameDraft = ref('')
const confirmDeleteId = ref<number | null>(null)
let confirmResetTimer: ReturnType<typeof setTimeout> | null = null

const activeSession = computed(
  () => props.sessions.find((session) => session.id === props.activeSessionId) ?? null,
)

const activeLabel = computed(() =>
  activeSession.value === null ? '选择会话' : sessionLabel(activeSession.value),
)

function sessionLabel(session: AgentSession): string {
  return session.title ?? props.titles[session.id] ?? formatFallbackTitle(session)
}

function formatFallbackTitle(session: AgentSession): string {
  const date = new Date(session.created_at)
  if (Number.isNaN(date.getTime())) return `会话 #${session.id}`
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

function formatRelativeTime(iso: string): string {
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return ''
  const diffMinutes = Math.max(0, Math.round((Date.now() - time) / 60000))
  if (diffMinutes < 1) return '刚刚'
  if (diffMinutes < 60) return `${diffMinutes} 分钟前`
  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours} 小时前`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 7) return `${diffDays} 天前`
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function onSelectItem(session: AgentSession): void {
  expanded.value = false
  if (session.id !== props.activeSessionId) emit('select', session.id)
}

function startRename(session: AgentSession): void {
  renamingId.value = session.id
  renameDraft.value = session.title ?? props.titles[session.id] ?? ''
}

function commitRename(): void {
  const sessionId = renamingId.value
  if (sessionId === null) return
  const value = renameDraft.value.trim()
  renamingId.value = null
  if (!value || value.length > SESSION_TITLE_MAX_LENGTH) return
  emit('rename', sessionId, value)
}

function cancelRename(): void {
  renamingId.value = null
}

function requestDelete(session: AgentSession): void {
  confirmDeleteId.value = session.id
  if (confirmResetTimer !== null) clearTimeout(confirmResetTimer)
  confirmResetTimer = setTimeout(() => {
    confirmDeleteId.value = null
  }, DELETE_CONFIRM_RESET_MS)
}

function confirmDelete(session: AgentSession): void {
  if (confirmResetTimer !== null) {
    clearTimeout(confirmResetTimer)
    confirmResetTimer = null
  }
  confirmDeleteId.value = null
  emit('delete', session.id)
}

onBeforeUnmount(() => {
  if (confirmResetTimer !== null) clearTimeout(confirmResetTimer)
})
</script>

<template>
  <div class="session-list" data-test="session-list">
    <div class="session-toolbar">
      <button
        type="button"
        class="session-toggle"
        data-test="session-toggle"
        :aria-expanded="expanded"
        :disabled="disabled"
        @click="expanded = !expanded"
      >
        <span class="toggle-title">{{ activeLabel }}</span>
        <span v-if="sessions.length > 1" class="count-badge">{{ sessions.length }}</span>
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" :class="{ flipped: expanded }">
          <path fill="currentColor" d="M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z" />
        </svg>
      </button>
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

    <ul v-if="expanded" class="session-items" data-test="session-items" aria-label="会话列表">
      <li
        v-for="session in sessions"
        :key="session.id"
        class="session-item"
        :class="{ active: session.id === activeSessionId }"
      >
        <template v-if="renamingId === session.id">
          <input
            v-model="renameDraft"
            class="rename-input"
            data-test="session-rename-input"
            maxlength="120"
            aria-label="会话标题"
            @keydown.enter.prevent="commitRename"
            @keydown.esc.prevent="cancelRename"
            @blur="commitRename"
          />
        </template>
        <template v-else>
          <button
            type="button"
            class="item-main"
            data-test="session-item"
            :aria-current="session.id === activeSessionId ? 'true' : undefined"
            @click="onSelectItem(session)"
          >
            <span class="item-title">{{ sessionLabel(session) }}</span>
            <span class="item-time">{{ formatRelativeTime(session.updated_at) }}</span>
          </button>
          <span class="item-actions">
            <button
              v-if="confirmDeleteId === session.id"
              type="button"
              class="confirm-btn"
              data-test="session-item-delete-confirm"
              @click="confirmDelete(session)"
            >
              确认删除
            </button>
            <button
              v-else
              type="button"
              class="icon-btn"
              data-test="session-item-delete"
              aria-label="删除会话"
              @click="requestDelete(session)"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"
                />
              </svg>
            </button>
            <button
              type="button"
              class="icon-btn"
              data-test="session-item-rename"
              aria-label="重命名会话"
              @click="startRename(session)"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"
                />
              </svg>
            </button>
          </span>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.session-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
  flex: 1;
}

.session-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.session-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  background: var(--fg-surface-sunken);
  color: var(--fg-ink);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  text-align: left;
}

.session-toggle:hover {
  border-color: var(--fg-line-strong);
}

.toggle-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.count-badge {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--fg-accent-soft);
  color: var(--fg-accent-ink);
  font-size: 11px;
  line-height: 16px;
}

.session-toggle svg {
  flex-shrink: 0;
  color: var(--fg-ink-secondary);
  transition: transform 0.15s ease;
}

.session-toggle svg.flipped {
  transform: rotate(180deg);
}

.session-items {
  margin: 6px 0 0;
  padding: 4px;
  list-style: none;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  background: var(--fg-surface-sunken);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.session-item {
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: var(--fg-radius-control);
  padding: 2px 4px;
}

.session-item.active {
  background: var(--fg-accent-soft);
}

.item-main {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border: none;
  border-radius: var(--fg-radius-control);
  background: transparent;
  color: var(--fg-ink);
  font-size: 12px;
  line-height: 1.4;
  text-align: left;
  cursor: pointer;
}

.session-item.active .item-main {
  color: var(--fg-accent-ink);
  font-weight: 600;
}

.item-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-time {
  color: var(--fg-ink-secondary);
  font-size: 11px;
}

.item-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--fg-radius-control);
  background: transparent;
  color: var(--fg-ink-secondary);
  cursor: pointer;
}

.icon-btn:hover {
  background: var(--fg-surface-raised);
  color: var(--fg-ink);
}

.confirm-btn {
  padding: 2px 8px;
  border: 1px solid var(--fg-status-disputed);
  border-radius: var(--fg-radius-control);
  background: transparent;
  color: var(--fg-status-disputed);
  font-size: 11px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
}

.confirm-btn:hover {
  background: var(--fg-surface-raised);
}

.rename-input {
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border: 1px solid var(--fg-accent);
  border-radius: var(--fg-radius-control);
  background: var(--fg-surface-raised);
  color: var(--fg-ink);
  font-size: 12px;
  line-height: 1.4;
  outline: none;
}
</style>
