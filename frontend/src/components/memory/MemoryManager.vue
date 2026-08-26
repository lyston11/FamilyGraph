<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

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
} from '@/types/memory'

const memory = useMemoryStore()
const spaces = useSpacesStore()

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

const scopeOptions = computed(() => {
  const options: { value: string; label: string }[] = [
    { value: 'private', label: MEMORY_SCOPE_LABELS.private },
  ]
  const space = currentSpace.value
  if (space) {
    options.push(
      { value: `household:${space.id}`, label: `${space.name} · 家庭共享` },
      { value: `lineage:${space.id}`, label: `${space.name} · 族谱共享` },
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

async function confirmCandidate(): Promise<void> {
  const candidate = selectedCandidate.value
  if (!candidate) return
  const shared = selectedScope.value !== 'private'
  if (shared && !candidateAllowsShared(candidate)) {
    ElMessage.warning('高敏感或必须本地处理的内容不能共享')
    return
  }
  savingCandidate.value = true
  try {
    await memory.confirmCandidate(candidate.id, selectedScope.value, retentionDays.value)
    ElMessage.success('记忆已确认，并按你选择的范围保存')
    selectedCandidate.value = null
  } catch (reason) {
    const message = reason instanceof ApiError
      ? friendlyMemoryError(reason.code, reason.message)
      : '确认失败，请稍后重试'
    ElMessage.error(message)
  } finally {
    savingCandidate.value = false
  }
}

async function dismissCandidate(candidate: MemoryCandidate): Promise<void> {
  candidateActionId.value = candidate.id
  try {
    await memory.dismissCandidate(candidate.id)
    ElMessage.success('候选记忆已忽略')
  } catch (reason) {
    ElMessage.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '操作失败，请稍后重试')
  } finally {
    candidateActionId.value = null
  }
}

async function removeMemory(item: Memory): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除后这条内容会立即从检索结果中失效，且无法恢复。',
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await memory.remove(item.id, item.space_id)
    ElMessage.success('记忆已删除')
  } catch (reason) {
    if (reason === 'cancel' || reason === 'close') return
    ElMessage.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '删除失败，请稍后重试')
  }
}

async function revokeMemory(item: Memory): Promise<void> {
  try {
    await memory.revoke(item.id, item.space_id)
    ElMessage.success('记忆已撤销，检索已立即失效')
  } catch (reason) {
    ElMessage.error(reason instanceof ApiError ? friendlyMemoryError(reason.code, reason.message) : '撤销失败，请稍后重试')
  }
}

function runSearch(): void {
  if (searchTimer.value) clearTimeout(searchTimer.value)
  const spaceId = currentSpace.value?.id
  if (spaceId === undefined) return
  searchTimer.value = setTimeout(() => {
    void memory.search(spaceId, query.value).catch(() => undefined)
  }, 250)
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '长期保留'
}

function scopeLabel(item: Memory): string {
  return item.scope === 'private'
    ? MEMORY_SCOPE_LABELS.private
    : `${item.scope === 'household' ? '家庭' : '族谱'}共享 · 空间 #${item.space_id ?? '—'}`
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
      <el-button text data-test="memory-refresh" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="memory.error"
      type="warning"
      :closable="false"
      class="error-alert"
      data-test="memory-error"
    >
      {{ memory.error.message }}
    </el-alert>

    <section class="knowledge-section" data-test="candidate-section">
      <div class="section-heading">
        <div>
          <h3>待确认记忆</h3>
          <p>候选卡保留原话、摘要和用途；确认 scope 前不会被任何会话检索。</p>
        </div>
        <el-switch
          v-model="showHistory"
          active-text="显示已处理"
          data-test="show-memory-history"
          @change="load"
        />
      </div>

      <el-empty
        v-if="pendingCandidates.length === 0 && !memory.candidatesLoading"
        description="暂无需要你确认的记忆候选"
        :image-size="68"
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
            <el-tag size="small" :type="candidate.status === 'pending' ? 'warning' : 'info'">
              {{ MEMORY_CANDIDATE_STATUS_LABELS[candidate.status] }}
            </el-tag>
            <el-tag size="small" type="info">
              {{ MEMORY_SENSITIVITY_LABELS[candidate.sensitivity] }}
            </el-tag>
          </div>
          <h4>{{ candidate.summary }}</h4>
          <blockquote>“{{ candidate.raw_quote }}”</blockquote>
          <div class="candidate-meta">
            <span>用途：{{ candidate.purpose }}</span>
            <span>建议：{{ MEMORY_SCOPE_LABELS[candidate.suggested_scope] }}</span>
            <span>抽取版本：{{ candidate.extractor_version }}</span>
          </div>
          <div v-if="candidate.status === 'pending'" class="candidate-actions">
            <el-button size="small" type="primary" data-test="confirm-candidate" @click="openCandidate(candidate)">
              选择范围并确认
            </el-button>
            <el-button
              size="small"
              :loading="candidateActionId === candidate.id"
              data-test="dismiss-candidate"
              @click="dismissCandidate(candidate)"
            >
              忽略
            </el-button>
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
      <el-empty
        v-if="allMemories.length === 0"
        description="还没有确认的记忆"
        :image-size="68"
        data-test="memory-empty"
      />
      <div v-else class="memory-list">
        <article v-for="item in allMemories" :key="item.id" class="memory-card" data-test="memory-card">
          <div class="memory-topline">
            <div>
              <span class="memory-title">{{ item.content }}</span>
              <p class="memory-scope">{{ scopeLabel(item) }} · 修订 {{ item.revision }}</p>
            </div>
            <el-tag size="small" :type="item.sensitivity === 'normal' ? 'success' : 'warning'">
              {{ MEMORY_SENSITIVITY_LABELS[item.sensitivity] }}
            </el-tag>
          </div>
          <blockquote>原话：“{{ item.raw_quote }}”</blockquote>
          <div class="memory-meta">
            <span>用途：{{ item.purpose }}</span>
            <span>保留至：{{ formatTime(item.retention_until) }}</span>
          </div>
          <div class="memory-actions">
            <el-button size="small" plain data-test="revoke-memory" @click="revokeMemory(item)">撤销检索</el-button>
            <el-button size="small" type="danger" plain data-test="delete-memory" @click="removeMemory(item)">删除</el-button>
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
      <el-alert v-if="!currentSpace" type="info" :closable="false">
        选择一个家庭空间后才能进行空间知识检索。
      </el-alert>
      <template v-else>
        <el-input
          v-model="query"
          clearable
          placeholder="搜索当前空间的已确认知识…"
          aria-label="搜索当前空间知识"
          data-test="rag-search-input"
          @input="runSearch"
        />
        <div v-loading="currentPartition?.ragLoading" class="rag-results" data-test="rag-results">
          <el-empty
            v-if="query.trim() && searchResults.length === 0 && !currentPartition?.ragLoading"
            description="没有命中当前空间允许的知识"
            :image-size="60"
            data-test="rag-empty"
          />
          <article v-for="result in searchResults" :key="result.citation_handle" class="rag-result" data-test="rag-result">
            <div class="result-meta">
              <el-tag size="small" type="success">{{ result.scope }}</el-tag>
              <span>{{ result.source_type }} · 来源 {{ result.source_id }} · 修订 {{ result.revision }}</span>
            </div>
            <p>{{ result.text }}</p>
            <code>{{ result.citation_handle }}</code>
          </article>
        </div>
      </template>
    </section>

    <el-dialog
      :model-value="selectedCandidate !== null"
      title="确认一条记忆"
      width="min(520px, calc(100vw - 32px))"
      data-test="confirm-memory-dialog"
      @update:model-value="(open: boolean) => !open && closeCandidate()"
    >
      <template v-if="selectedCandidate">
        <p class="dialog-summary">{{ selectedCandidate.summary }}</p>
        <blockquote>“{{ selectedCandidate.raw_quote }}”</blockquote>
        <el-form label-position="top">
          <el-form-item label="保存范围">
            <el-select v-model="selectedScope" class="full-width" data-test="memory-scope-select">
              <el-option
                v-for="option in scopeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
                :disabled="option.value !== 'private' && !candidateAllowsShared(selectedCandidate)"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="保留期限（可选）">
            <el-input-number
              v-model="retentionDays"
              :min="1"
              :max="3650"
              controls-position="right"
              placeholder="不填写表示长期保留"
              data-test="memory-retention-days"
            />
            <span class="field-hint">天；系统不会自动扩大你选择的 scope。</span>
          </el-form-item>
        </el-form>
        <el-alert
          v-if="!candidateAllowsShared(selectedCandidate)"
          type="warning"
          :closable="false"
          data-test="memory-sharing-warning"
        >
          {{ MEMORY_SENSITIVITY_LABELS[selectedCandidate.sensitivity] }}内容只能保存在「仅我可见」范围。
        </el-alert>
      </template>
      <template #footer>
        <el-button @click="closeCandidate">取消</el-button>
        <el-button type="primary" :loading="savingCandidate" data-test="confirm-memory-submit" @click="confirmCandidate">
          确认保存
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.memory-manager {
  --ink: #18343a;
  --muted: #6d8585;
  --line: #dce9e4;
  --wash: #f3f8f5;
  color: var(--ink);
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
  border-bottom: 1px solid var(--line);
}

.eyebrow {
  margin: 0 0 5px;
  color: #18816b;
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
  font-family: Georgia, 'Noto Serif SC', serif;
  font-size: 25px;
  font-weight: 600;
}

.description,
.section-heading p,
.candidate-meta,
.memory-meta,
.memory-scope,
.field-hint {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.description {
  max-width: 560px;
  margin-bottom: 0;
}

.knowledge-section {
  padding: 22px 0;
  border-bottom: 1px solid var(--line);
}

.section-heading {
  align-items: flex-start;
  margin-bottom: 14px;
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
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 5px 18px rgb(24 52 58 / 5%);
}

.candidate-card {
  border-left: 3px solid #e3a52e;
}

.candidate-card.handled {
  border-left-color: #a9bbb4;
  opacity: 0.76;
}

.candidate-topline,
.result-meta {
  flex-wrap: wrap;
  gap: 7px;
}

.candidate-id {
  color: var(--muted);
  font-size: 11px;
}

.candidate-card h4 {
  margin: 10px 0 7px;
  font-size: 15px;
}

blockquote {
  margin: 8px 0;
  padding: 8px 11px;
  border-left: 2px solid #b8d7ca;
  background: var(--wash);
  color: #426160;
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

.memory-card {
  background: linear-gradient(120deg, #fff, #fbfdfc);
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
  border-left: 3px solid #4aa78a;
}

.result-meta {
  color: var(--muted);
  font-size: 11px;
}

.rag-result p {
  margin: 9px 0 5px;
  color: var(--ink);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.rag-result code {
  color: var(--muted);
  font-size: 10px;
  word-break: break-all;
}

.dialog-summary {
  font-weight: 600;
  line-height: 1.5;
}

.full-width {
  width: 100%;
}

.field-hint {
  display: block;
  margin-top: 4px;
}

.error-alert {
  margin-top: 14px;
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
