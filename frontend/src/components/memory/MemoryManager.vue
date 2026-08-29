<script setup lang="ts">
import { useDialog, useMessage } from 'naive-ui'
import {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'

import { ApiError } from '@/api/errors'
import { friendlyMemoryError } from '@/api/memory'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import {
  MEMORY_CANDIDATE_STATUS_LABELS,
  MEMORY_SCOPE_LABELS,
  MEMORY_SENSITIVITY_LABELS,
  type Memory,
  type MemoryCandidate,
  type MemoryScope,
  type MemorySensitivity,
} from '@/types/memory'

const memory = useMemoryStore()
const spaces = useSpacesStore()
const message = useMessage()
const dialog = useDialog()

const showHistory = ref(false)
const selectedCandidate = ref<MemoryCandidate | null>(null)
const selectedScope = ref<MemoryScope>('private')
const retentionDays = ref<number | null>(null)
const savingCandidate = ref(false)
const candidateActionId = ref<number | null>(null)
const query = ref('')
const searchTimer = ref<ReturnType<typeof setTimeout> | null>(null)

const currentSpace = computed(() => spaces.currentSpace)
const currentPartition = computed(() => {
  const spaceId = currentSpace.value?.id
  return spaceId === undefined ? null : memory.partitionOf(spaceId)
})
const sharedMemories = computed(() =>
  (currentPartition.value?.memories ?? []).filter((item) => item.space_id !== null),
)
const allMemories = computed(() => [...memory.privateMemories, ...sharedMemories.value])
const searchResults = computed(() => currentPartition.value?.ragResults ?? [])
const pendingCandidates = computed(() =>
  showHistory.value ? memory.candidates : memory.pendingCandidates,
)

const scopeOptions = computed<SelectOption[]>(() => {
  const options: SelectOption[] = [
    { value: 'private', label: MEMORY_SCOPE_LABELS.private },
  ]
  const space = currentSpace.value
  if (space) {
    // 高敏感/必须本地处理的候选：共享选项置灰（与迁移前 el-option :disabled 行为对齐）
    const sharedDisabled =
      selectedCandidate.value !== null && !candidateAllowsShared(selectedCandidate.value)
    options.push(
      {
        value: `household:${space.id}`,
        label: `${space.name} · 家庭共享`,
        disabled: sharedDisabled,
      },
      {
        value: `lineage:${space.id}`,
        label: `${space.name} · 族谱共享`,
        disabled: sharedDisabled,
      },
    )
  }
  return options
})

onMounted(() => {
  void load()
})

watch(
  () => currentSpace.value?.id,
  (spaceId, previousId) => {
    if (typeof previousId === 'number' && previousId !== spaceId) memory.resetForSpace(previousId)
    if (typeof spaceId === 'number') void memory.ensureMemories(spaceId).catch(() => undefined)
  },
)

async function load(): Promise<void> {
  await Promise.all([
    memory.loadCandidates(showHistory.value).catch(() => undefined),
    memory.loadPrivateMemories().catch(() => undefined),
    currentSpace.value
      ? memory.ensureMemories(currentSpace.value.id).catch(() => undefined)
      : Promise.resolve(),
  ])
}

function openCandidate(candidate: MemoryCandidate): void {
  selectedCandidate.value = candidate
  selectedScope.value = 'private'
  retentionDays.value = null
}

function candidateAllowsShared(candidate: MemoryCandidate): boolean {
  return candidate.sensitivity !== 'high' && candidate.sensitivity !== 'local_required'
}

function closeCandidate(): void {
  if (!savingCandidate.value) selectedCandidate.value = null
}

function onScopeSelect(value: string | number | Array<string | number> | null): void {
  // options 只产出合同内的 scope 字符串（type-safety.md：不改写枚举）
  if (typeof value === 'string') selectedScope.value = value as MemoryScope
}

async function confirmCandidate(): Promise<void> {
  const candidate = selectedCandidate.value
  if (!candidate) return
  const shared = selectedScope.value !== 'private'
  if (shared && !candidateAllowsShared(candidate)) {
    message.warning('高敏感或必须本地处理的内容不能共享')
    return
  }
  savingCandidate.value = true
  try {
    await memory.confirmCandidate(candidate.id, selectedScope.value, retentionDays.value)
    message.success('记忆已确认，并按你选择的范围保存')
    selectedCandidate.value = null
  } catch (reason) {
    const messageText = reason instanceof ApiError
      ? friendlyMemoryError(reason.code, reason.message)
      : '确认失败，请稍后重试'
    message.error(messageText)
  } finally {
    savingCandidate.value = false
  }
}

async function dismissCandidate(candidate: MemoryCandidate): Promise<void> {
  candidateActionId.value = candidate.id
  try {
    await memory.dismissCandidate(candidate.id)
    message.success('候选记忆已忽略')
  } catch (reason) {
    message.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '操作失败，请稍后重试')
  } finally {
    candidateActionId.value = null
  }
}

function removeMemory(item: Memory): void {
  dialog.warning({
    title: '删除确认',
    content: '删除后这条内容会立即从检索结果中失效，且无法恢复。',
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => {
      void (async () => {
        try {
          await memory.remove(item.id, item.space_id)
          message.success('记忆已删除')
        } catch (reason) {
          message.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '删除失败，请稍后重试')
        }
      })()
    },
  })
}

async function revokeMemory(item: Memory): Promise<void> {
  try {
    await memory.revoke(item.id, item.space_id)
    message.success('记忆已撤销，检索已立即失效')
  } catch (reason) {
    message.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '撤销失败，请稍后重试')
  }
}

function onQueryInput(value: string): void {
  query.value = value
  runSearch()
}

function runSearch(): void {
  if (searchTimer.value) clearTimeout(searchTimer.value)
  const spaceId = currentSpace.value?.id
  if (spaceId === undefined) return
  searchTimer.value = setTimeout(() => {
    void memory.search(spaceId, query.value).catch(() => undefined)
  }, 250)
}

function onHistorySwitch(value: boolean): void {
  showHistory.value = value
  void load()
}

function onRetentionInput(value: number | null): void {
  retentionDays.value = value
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '长期保留'
}

function scopeLabel(item: Memory): string {
  return item.scope === 'private'
    ? MEMORY_SCOPE_LABELS.private
    : `${item.scope === 'household' ? '家庭' : '族谱'}共享 · 空间 #${item.space_id ?? '—'}`
}

/** 敏感等级徽章阶（design.md §3.4）：normal=confirmed / 敏感系=proposed / 高危=disputed */
function sensitivityBadge(sensitivity: MemorySensitivity): string {
  if (sensitivity === 'normal') return 'fg-badge--confirmed'
  if (sensitivity === 'high' || sensitivity === 'local_required') return 'fg-badge--disputed'
  return 'fg-badge--proposed'
}
</script>

<template>
  <section class="memory-manager" data-test="memory-manager">
    <div class="intro">
      <div>
        <p class="eyebrow">LONG-TERM KNOWLEDGE</p>
        <h2>记忆与知识</h2>
        <p class="description">
          原始聊天不会自动进入检索。只有你确认的记忆，或明确授权的材料，才会成为可追溯的知识来源。
        </p>
      </div>
      <NButton quaternary data-test="memory-refresh" @click="load">刷新</NButton>
    </div>

    <!-- Policy Guard / 服务端错误不静默（V2.5 合同）：保留可解释的错误状态 -->
    <NAlert
      v-if="memory.error"
      type="warning"
      :show-icon="true"
      :closable="false"
      class="error-alert"
      data-test="memory-error"
    >
      {{ memory.error.message }}
    </NAlert>

    <section class="knowledge-section" data-test="candidate-section">
      <div class="section-heading">
        <div>
          <h3>待确认记忆</h3>
          <p>候选卡保留原话、摘要和用途；确认 scope 前不会被任何会话检索。</p>
        </div>
        <div class="history-toggle">
          <span class="history-label">显示已处理</span>
          <NSwitch
            :value="showHistory"
            size="small"
            data-test="show-memory-history"
            aria-label="显示已处理的记忆候选"
            @update:value="onHistorySwitch"
          />
        </div>
      </div>

      <NEmpty
        v-if="pendingCandidates.length === 0 && !memory.candidatesLoading"
        description="暂无需要你确认的记忆候选"
        size="small"
        data-test="candidate-empty"
      />
      <div v-else class="candidate-list">
        <article
          v-for="candidate in pendingCandidates"
          :key="candidate.id"
          class="candidate-card"
          :class="{ handled: candidate.status !== 'pending' }"
          data-test="candidate-card"
        >
          <div class="candidate-topline">
            <span class="candidate-id">候选 #{{ candidate.id }}</span>
            <span class="fg-badge" :class="candidate.status === 'pending' ? 'fg-badge--proposed' : 'fg-badge--neutral'">
              {{ MEMORY_CANDIDATE_STATUS_LABELS[candidate.status] }}
            </span>
            <span class="fg-badge" :class="sensitivityBadge(candidate.sensitivity)">
              {{ MEMORY_SENSITIVITY_LABELS[candidate.sensitivity] }}
            </span>
          </div>
          <h4>{{ candidate.summary }}</h4>
          <blockquote>“{{ candidate.raw_quote }}”</blockquote>
          <div class="candidate-meta">
            <span>用途：{{ candidate.purpose }}</span>
            <span>建议：{{ MEMORY_SCOPE_LABELS[candidate.suggested_scope] }}</span>
            <span>抽取版本：{{ candidate.extractor_version }}</span>
          </div>
          <div v-if="candidate.status === 'pending'" class="candidate-actions">
            <NButton size="small" type="primary" data-test="confirm-candidate" @click="openCandidate(candidate)">
              选择范围并确认
            </NButton>
            <NButton
              size="small"
              secondary
              :loading="candidateActionId === candidate.id"
              data-test="dismiss-candidate"
              @click="dismissCandidate(candidate)"
            >
              忽略
            </NButton>
          </div>
        </article>
      </div>
    </section>

    <section class="knowledge-section" data-test="memory-section">
      <div class="section-heading">
        <div>
          <h3>已确认记忆</h3>
          <p>内容按 private / 当前空间隔离；撤销或删除后旧索引立即失效。</p>
        </div>
      </div>
      <NEmpty
        v-if="allMemories.length === 0"
        description="还没有确认的记忆"
        size="small"
        data-test="memory-empty"
      />
      <div v-else class="memory-list">
        <article v-for="item in allMemories" :key="item.id" class="memory-card" data-test="memory-card">
          <div class="memory-topline">
            <div>
              <span class="memory-title">{{ item.content }}</span>
              <p class="memory-scope">{{ scopeLabel(item) }} · 修订 {{ item.revision }}</p>
            </div>
            <span class="fg-badge" :class="sensitivityBadge(item.sensitivity)">
              {{ MEMORY_SENSITIVITY_LABELS[item.sensitivity] }}
            </span>
          </div>
          <blockquote>原话：“{{ item.raw_quote }}”</blockquote>
          <div class="memory-meta">
            <span>用途：{{ item.purpose }}</span>
            <span>保留至：{{ formatTime(item.retention_until) }}</span>
          </div>
          <div class="memory-actions">
            <NButton size="small" secondary data-test="revoke-memory" @click="revokeMemory(item)">撤销检索</NButton>
            <NButton size="small" type="error" secondary data-test="delete-memory" @click="removeMemory(item)">删除</NButton>
          </div>
        </article>
      </div>
    </section>

    <section class="knowledge-section rag-section" data-test="rag-section">
      <div class="section-heading">
        <div>
          <h3>空间知识检索</h3>
          <p>只搜索当前空间允许的已确认材料；每条结果都带有可追溯 citation handle。</p>
        </div>
      </div>
      <NAlert v-if="!currentSpace" type="info" :show-icon="true" :closable="false">
        选择一个家庭空间后才能进行空间知识检索。
      </NAlert>
      <template v-else>
        <NInput
          :value="query"
          clearable
          placeholder="搜索当前空间的已确认知识…"
          data-test="rag-search-input"
          @update:value="onQueryInput"
        />
        <NSpin :show="currentPartition?.ragLoading === true">
          <div class="rag-results" data-test="rag-results">
            <NEmpty
              v-if="query.trim() && searchResults.length === 0 && !currentPartition?.ragLoading"
              description="没有命中当前空间允许的知识"
              size="small"
              data-test="rag-empty"
            />
            <article v-for="result in searchResults" :key="result.citation_handle" class="rag-result" data-test="rag-result">
              <div class="result-meta">
                <span class="fg-badge fg-badge--confirmed">{{ result.scope }}</span>
                <span>{{ result.source_type }} · 来源 {{ result.source_id }} · 修订 {{ result.revision }}</span>
              </div>
              <p>{{ result.text }}</p>
              <code>{{ result.citation_handle }}</code>
            </article>
          </div>
        </NSpin>
      </template>
    </section>

    <!-- 候选确认弹层：确认前完整展示原话、摘要、scope、敏感等级与保留期限（V2.5 合同） -->
    <NModal
      :show="selectedCandidate !== null"
      preset="card"
      title="确认一条记忆"
      data-test="confirm-memory-dialog"
      @update:show="(open: boolean) => !open && closeCandidate()"
    >
      <template v-if="selectedCandidate">
        <div class="dialog-topline">
          <!-- V2.5 合同：确认时敏感等级必须可见（不依赖列表卡） -->
          <span
            class="fg-badge"
            :class="sensitivityBadge(selectedCandidate.sensitivity)"
            data-test="confirm-memory-sensitivity"
          >
            敏感等级：{{ MEMORY_SENSITIVITY_LABELS[selectedCandidate.sensitivity] }}
          </span>
        </div>
        <p class="dialog-summary">{{ selectedCandidate.summary }}</p>
        <blockquote>“{{ selectedCandidate.raw_quote }}”</blockquote>
        <div class="dialog-fields">
          <label class="field-label">保存范围</label>
          <NSelect
            :value="selectedScope"
            :options="scopeOptions"
            :consistent-menu-width="false"
            data-test="memory-scope-select"
            aria-label="选择保存范围"
            @update:value="onScopeSelect"
          />
          <label class="field-label">保留期限（可选）</label>
          <div class="retention-row">
            <NInputNumber
              :value="retentionDays"
              :min="1"
              :max="3650"
              placeholder="不填写表示长期保留"
              data-test="memory-retention-days"
              aria-label="保留期限天数"
              @update:value="onRetentionInput"
            />
            <span class="field-hint">天；系统不会自动扩大你选择的 scope。</span>
          </div>
        </div>
        <NAlert
          v-if="!candidateAllowsShared(selectedCandidate)"
          type="warning"
          :show-icon="true"
          :closable="false"
          data-test="memory-sharing-warning"
        >
          {{ MEMORY_SENSITIVITY_LABELS[selectedCandidate.sensitivity] }}内容只能保存在「仅我可见」范围。
        </NAlert>
      </template>
      <template #footer>
        <div class="modal-actions">
          <NButton @click="closeCandidate">取消</NButton>
          <NButton type="primary" :loading="savingCandidate" data-test="confirm-memory-submit" @click="confirmCandidate">
            确认保存
          </NButton>
        </div>
      </template>
    </NModal>
  </section>
</template>

<style scoped>
.memory-manager {
  color: var(--fg-ink);
}

.intro,
.section-heading,
.memory-topline,
.candidate-topline,
.candidate-actions,
.memory-actions,
.memory-meta,
.candidate-meta,
.result-meta {
  display: flex;
  align-items: center;
}

.intro,
.section-heading,
.memory-topline {
  justify-content: space-between;
  gap: 16px;
}

.intro {
  padding: 2px 0 18px;
  border-bottom: 1px solid var(--fg-line);
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--fg-ink-faint);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.16em;
}

h2,
h3,
h4,
p {
  margin-top: 0;
}

h2 {
  margin-bottom: 6px;
  font-family: var(--fg-font-display);
  font-size: 25px;
  font-weight: 600;
}

.description,
.section-heading p,
.candidate-meta,
.memory-meta,
.memory-scope,
.field-hint {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.description {
  max-width: 560px;
  margin-bottom: 0;
}

.knowledge-section {
  padding: 22px 0;
  border-bottom: 1px solid var(--fg-line);
}

.section-heading {
  align-items: flex-start;
  margin-bottom: 14px;
}

.history-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.history-label {
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

h3 {
  margin-bottom: 4px;
  font-size: 16px;
}

.section-heading p {
  margin-bottom: 0;
}

.candidate-list,
.memory-list,
.rag-results {
  display: grid;
  gap: 10px;
}

.candidate-card,
.memory-card,
.rag-result {
  padding: 15px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface-raised);
  box-shadow: var(--fg-shadow-card);
}

/* 状态左缘线 = 领域状态语义（--fg-status-*，design.md §3.4，无新色） */
.candidate-card {
  border-left: 3px solid var(--fg-status-proposed);
}

.candidate-card.handled {
  border-left-color: var(--fg-ink-faint);
  opacity: 0.76;
}

.candidate-topline,
.result-meta {
  flex-wrap: wrap;
  gap: 7px;
}

.candidate-id {
  color: var(--fg-ink-secondary);
  font-size: 11px;
}

.candidate-card h4 {
  margin: 10px 0 7px;
  font-size: 15px;
}

blockquote {
  margin: 8px 0;
  padding: 8px 11px;
  border-left: 2px solid color-mix(in srgb, var(--fg-status-confirmed) 45%, transparent);
  background: var(--fg-surface-sunken);
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.candidate-meta,
.memory-meta {
  flex-wrap: wrap;
  gap: 4px 14px;
}

.candidate-actions,
.memory-actions {
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}

.memory-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.memory-scope {
  margin: 3px 0 0;
}

.rag-section {
  border-bottom: 0;
}

.rag-results {
  margin-top: 12px;
}

.rag-result {
  border-left: 3px solid var(--fg-status-confirmed);
}

.result-meta {
  color: var(--fg-ink-secondary);
  font-size: 11px;
}

.rag-result p {
  margin: 9px 0 5px;
  color: var(--fg-ink);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.rag-result code {
  color: var(--fg-ink-secondary);
  font-size: 10px;
  word-break: break-all;
}

.dialog-summary {
  font-weight: 600;
  line-height: 1.5;
}

.dialog-topline {
  display: flex;
  align-items: center;
}

.dialog-fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0;
}

.field-label {
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.retention-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.field-hint {
  display: block;
}

.error-alert {
  margin-top: 14px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 600px) {
  .intro,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .candidate-actions,
  .memory-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
