<script setup lang="ts">
// 记忆与知识五标签容器（PRD §2.5 / design §5.4，09-01 Phase 5）：
// - 五个标签（移动端 CSS 分段控制器外观）：待确认 / 我的私有记忆 / 当前家庭共享 /
//   当前家族共享 / 检索与引用；有待确认候选时默认进入待确认，否则默认私有；
// - 候选 / 正式记忆 / 检索结果视觉状态分离（icon+文字徽章，不只靠颜色）；
// - 私有记忆：新增（只能新建候选）/ 撤销 / 删除，默认 scope=private；
//   家庭/家族共享内容只能经候选确认（含目标 scope 与隐私影响）产生；
// - 所有写入/撤销/删除/确认完成后由 store 重读服务端状态（无乐观本地副本）；
// - scope 标签是对已授权数据的展示层过滤，不做前端授权推导；
// - 数据全部经 memory store（服务端真源），组件不发请求。
import { NAlert, NButton, NEmpty, NSpin, NSwitch, NTabPane, NTabs, useDialog } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'

import MemoryCandidateConfirmDialog from './MemoryCandidateConfirmDialog.vue'
import MemoryCardItem from './MemoryCardItem.vue'
import MemoryEditorDialog, { type MemoryEditorInitial } from './MemoryEditorDialog.vue'
import MemoryRagPanel from './MemoryRagPanel.vue'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import {
  MEMORY_CANDIDATE_STATUS_LABELS,
  MEMORY_SCOPE_LABELS,
  MEMORY_SENSITIVITY_LABELS,
  type Memory,
  type MemoryCandidate,
} from '@/types/memory'

type MemoryTabId = 'pending' | 'private' | 'household' | 'lineage' | 'rag'

const TAB_LABELS: Record<Exclude<MemoryTabId, 'pending'>, string> = {
  private: '我的私有记忆',
  household: '当前家庭共享',
  lineage: '当前家族共享',
  rag: '检索与引用',
}

const memory = useMemoryStore()
const spaces = useSpacesStore()
const dialog = useDialog()

/** null = 尚未按默认规则初始化（候选加载完成后决定默认标签） */
const activeTab = ref<MemoryTabId | null>(null)
const showHistory = ref(false)
const confirmCandidate = ref<MemoryCandidate | null>(null)
const editorOpen = ref(false)
const editorInitial = ref<MemoryEditorInitial | null>(null)
const candidateActionId = ref<number | null>(null)

const currentSpace = computed(() => spaces.currentSpace)
const pendingCandidates = computed(() =>
  showHistory.value ? memory.candidates : memory.pendingCandidates,
)
const pendingTabLabel = computed(() => {
  const count = memory.pendingCandidates.length
  return count > 0 ? `待确认（${count}）` : '待确认'
})

// ---- 各标签的记忆列表（对已授权数据的展示层过滤，不做前端授权推导） ----
const privateMemories = computed<Memory[]>(() =>
  memory.privateMemories.filter((item) => item.scope === 'private'),
)
const sharedMemories = computed<Memory[]>(() => {
  const spaceId = currentSpace.value?.id
  if (spaceId === undefined) return []
  return (memory.partitionOf(spaceId)?.memories ?? []).filter(
    (item) => item.space_id === spaceId && item.scope !== 'private',
  )
})
const householdMemories = computed(() =>
  sharedMemories.value.filter((item) => item.scope === 'household'),
)
const lineageMemories = computed(() =>
  sharedMemories.value.filter((item) => item.scope === 'lineage'),
)

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
  // 默认标签规则（PRD §2.5）：有待确认候选 → 待确认，否则 → 私有
  if (activeTab.value === null) {
    activeTab.value = memory.pendingCandidates.length > 0 ? 'pending' : 'private'
  }
}

function onHistorySwitch(value: boolean): void {
  showHistory.value = value
  void memory.loadCandidates(value).catch(() => undefined)
}

// ---- 候选（待确认标签）----
function openCandidate(candidate: MemoryCandidate): void {
  confirmCandidate.value = candidate
}

async function dismissCandidate(candidate: MemoryCandidate): Promise<void> {
  candidateActionId.value = candidate.id
  try {
    await memory.dismissCandidate(candidate.id)
  } catch (reason) {
    // 服务端拒绝不静默（V2.5 合同）：保留可观测错误记录，列表保持服务端原状
    console.error(reason)
  } finally {
    candidateActionId.value = null
  }
}

// ---- 私有记忆（新增 = 只能新建候选）----
function openEditor(): void {
  editorInitial.value = null
  editorOpen.value = true
}

// ---- 撤销 / 删除（正式记忆；store 成功后重读服务端状态）----
function revokeMemory(item: Memory): void {
  void memory.revoke(item.id, item.space_id).catch((reason: unknown) => {
    console.error(reason)
  })
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
        } catch (reason) {
          console.error(reason)
        }
      })()
    },
  })
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

    <NTabs
      :value="activeTab ?? undefined"
      type="line"
      class="memory-tabs"
      data-test="memory-tabs"
      @update:value="(value: string | number) => (activeTab = value as MemoryTabId)"
    >
      <!-- 标签 1：待确认（候选；只能确认/拒绝/稍后处理） -->
      <NTabPane name="pending" :tab="pendingTabLabel">
        <section class="tab-section" data-test="candidate-section">
          <div class="section-heading">
            <p class="section-hint">
              候选保留原话、摘要和用途；确认 scope 前不会被任何会话检索。
            </p>
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
          <NSpin
            v-else-if="memory.candidatesLoading && pendingCandidates.length === 0"
            :show="true"
            class="tab-spin"
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
                <!-- 候选状态：icon+文字（与正式记忆、检索结果视觉分离） -->
                <span
                  class="fg-badge"
                  :class="candidate.status === 'pending' ? 'fg-badge--proposed' : 'fg-badge--neutral'"
                  data-test="candidate-state-badge"
                >
                  <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
                    <path d="M12 2 3 6v6c0 5 3.8 9.1 9 10 5.2-.9 9-5 9-10V6z" />
                  </svg>
                  {{ candidate.status === 'pending' ? '候选 · 未进入检索' : MEMORY_CANDIDATE_STATUS_LABELS[candidate.status] }}
                </span>
                <span class="fg-badge" :class="candidate.sensitivity === 'normal' ? 'fg-badge--confirmed' : 'fg-badge--disputed'">
                  敏感等级：{{ MEMORY_SENSITIVITY_LABELS[candidate.sensitivity] }}
                </span>
              </div>
              <h4>{{ candidate.summary }}</h4>
              <blockquote>“{{ candidate.raw_quote }}”</blockquote>
              <div class="candidate-meta">
                <span>用途：{{ candidate.purpose }}</span>
                <span>建议：{{ MEMORY_SCOPE_LABELS[candidate.suggested_scope] }}</span>
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
      </NTabPane>

      <!-- 标签 2：我的私有记忆（默认 scope=private；新增/撤销/删除） -->
      <NTabPane name="private" :tab="TAB_LABELS.private">
        <section class="tab-section" data-test="private-section">
          <div class="section-heading">
            <p class="section-hint">仅本人可见；撤销或删除后旧索引立即失效。</p>
            <NButton size="small" type="primary" secondary data-test="add-memory" @click="openEditor">
              新增记忆
            </NButton>
          </div>
          <NEmpty
            v-if="privateMemories.length === 0"
            description="还没有仅你可见的记忆"
            size="small"
            data-test="private-empty"
          />
          <div v-else class="memory-list">
            <MemoryCardItem
              v-for="item in privateMemories"
              :key="item.id"
              :item="item"
              @revoke="revokeMemory(item)"
              @remove="removeMemory(item)"
            />
          </div>
        </section>
      </NTabPane>

      <!-- 标签 3：当前家庭共享 -->
      <NTabPane name="household" :tab="TAB_LABELS.household">
        <section class="tab-section" data-test="household-shared-section">
          <p class="section-hint">
            当前家庭空间（{{ currentSpace?.name ?? '未选择' }}）的共享记忆；由候选确认时的目标 scope 决定。
          </p>
          <NEmpty
            v-if="householdMemories.length === 0"
            description="当前家庭空间还没有共享记忆"
            size="small"
            data-test="household-shared-empty"
          />
          <div v-else class="memory-list">
            <MemoryCardItem
              v-for="item in householdMemories"
              :key="item.id"
              :item="item"
              @revoke="revokeMemory(item)"
              @remove="removeMemory(item)"
            />
          </div>
        </section>
      </NTabPane>

      <!-- 标签 4：当前家族共享 -->
      <NTabPane name="lineage" :tab="TAB_LABELS.lineage">
        <section class="tab-section" data-test="lineage-shared-section">
          <p class="section-hint">
            当前家族空间（{{ currentSpace?.name ?? '未选择' }}）的共享记忆；由候选确认时的目标 scope 决定。
          </p>
          <NEmpty
            v-if="lineageMemories.length === 0"
            description="当前家族空间还没有共享记忆"
            size="small"
            data-test="lineage-shared-empty"
          />
          <div v-else class="memory-list">
            <MemoryCardItem
              v-for="item in lineageMemories"
              :key="item.id"
              :item="item"
              @revoke="revokeMemory(item)"
              @remove="removeMemory(item)"
            />
          </div>
        </section>
      </NTabPane>

      <!-- 标签 5：检索与引用（只读 + 保存只能新建候选） -->
      <NTabPane name="rag" :tab="TAB_LABELS.rag">
        <MemoryRagPanel />
      </NTabPane>
    </NTabs>

    <!-- 候选确认弹层（抽取组件）：原话/摘要/用途/敏感等级/scope/隐私影响确认前可见 -->
    <MemoryCandidateConfirmDialog
      :candidate="confirmCandidate"
      @close="confirmCandidate = null"
    />

    <!-- 新增记忆（私有标签）：提交只能新建候选，进入待确认流程 -->
    <MemoryEditorDialog v-model:show="editorOpen" :initial="editorInitial" />
  </section>
</template>

<style scoped>
.memory-manager {
  color: var(--fg-ink);
}

.intro,
.section-heading,
.candidate-topline,
.candidate-actions,
.memory-meta,
.candidate-meta {
  display: flex;
  align-items: center;
}

.intro,
.section-heading {
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
.section-hint,
.candidate-meta,
.memory-scope {
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.description {
  max-width: 560px;
  margin-bottom: 0;
}

.memory-tabs {
  margin-top: 8px;
}

.tab-section {
  padding: 14px 0 20px;
}

.section-heading {
  align-items: flex-start;
  margin-bottom: 14px;
}

.section-hint {
  margin: 0 0 10px;
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

.tab-spin {
  min-height: 80px;
}

.candidate-list,
.memory-list {
  display: grid;
  gap: 10px;
}

.candidate-card {
  padding: 15px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface-raised);
  box-shadow: var(--fg-shadow-card);
}

/* 候选 = proposed 左缘线（与正式记忆、检索结果视觉分离的一部分） */
.candidate-card {
  border-left: 3px solid var(--fg-status-proposed);
}

.candidate-card.handled {
  border-left-color: var(--fg-ink-faint);
  opacity: 0.76;
}

.candidate-topline {
  flex-wrap: wrap;
  gap: 7px;
}

.candidate-topline .fg-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
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

.candidate-meta {
  flex-wrap: wrap;
  gap: 4px 14px;
}

.candidate-actions {
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}

.memory-list {
  margin-top: 4px;
}

.error-alert {
  margin-top: 14px;
}

/* 移动端（≤600px）：标签栏变分段控制器外观（PRD §2.5），无新颜色 */
@media (max-width: 600px) {
  .intro,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .candidate-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .memory-manager :deep(.n-tabs .n-tabs-nav) {
    padding: 3px;
    border: 1px solid var(--fg-line);
    border-radius: var(--fg-radius-control);
    background: var(--fg-surface-sunken);
  }

  .memory-manager :deep(.n-tabs .n-tabs-tab) {
    justify-content: center;
    min-height: 44px;
    padding: 8px 6px;
  }

  .memory-manager :deep(.n-tabs .n-tabs-tab--active) {
    font-weight: 700;
  }

  .memory-manager :deep(.n-tabs .n-tabs-pad),
  .memory-manager :deep(.n-tabs .n-tabs-tab-pad) {
    display: none;
  }
}
</style>
