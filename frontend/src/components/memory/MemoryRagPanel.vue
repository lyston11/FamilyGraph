<script setup lang="ts">
// 空间知识检索面板（PRD §2.5「检索与引用」标签）：
// - 检索结果只读：展示 citation_handle、source、scope、revision 与敏感等级标签
//   （当前 RAG 合同无独立 trust 字段，见任务 notes.md 差异记录）；
// - 「保存」只能新建候选：打开共用编辑器预填原文，提交走 POST /memory-candidates；
// - 数据经 memory store（空间分区 + epoch），组件不发请求。
import { NAlert, NButton, NEmpty, NInput, NSpin } from 'naive-ui'
import { computed, ref } from 'vue'

import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import MemoryEditorDialog, { type MemoryEditorInitial } from './MemoryEditorDialog.vue'
import type { MemoryCitation } from '@/types/memory'

const memory = useMemoryStore()
const spaces = useSpacesStore()

const query = ref('')
const searchTimer = ref<ReturnType<typeof setTimeout> | null>(null)
const editorOpen = ref(false)
const editorInitial = ref<MemoryEditorInitial | null>(null)

const currentSpace = computed(() => spaces.currentSpace)
const partition = computed(() => {
  const spaceId = currentSpace.value?.id
  return spaceId === undefined ? null : memory.partitionOf(spaceId)
})
const results = computed(() => partition.value?.ragResults ?? [])

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

/** 检索结果「保存」：只新建候选（预填原文），不做直接写入 */
function saveResult(result: MemoryCitation): void {
  editorInitial.value = {
    raw_quote: result.text ?? '',
    summary: result.text ? result.text.slice(0, 60) : '',
    suggested_scope: 'private',
  }
  editorOpen.value = true
}
</script>

<template>
  <section class="rag-panel" data-test="rag-panel">
    <NAlert v-if="!currentSpace" type="info" :show-icon="true" :closable="false">
      选择一个空间后才能进行知识检索。
    </NAlert>
    <template v-else>
      <NInput
        :value="query"
        clearable
        placeholder="搜索当前空间允许的已确认知识…"
        data-test="rag-search-input"
        @update:value="onQueryInput"
      />
      <NSpin :show="partition?.ragLoading === true">
        <div class="rag-results" data-test="rag-results">
          <NEmpty
            v-if="query.trim() && results.length === 0 && !partition?.ragLoading"
            description="没有命中当前空间允许的知识"
            size="small"
            data-test="rag-empty"
          />
          <NEmpty
            v-else-if="!query.trim()"
            description="输入关键词检索当前空间的已确认知识"
            size="small"
            data-test="rag-idle"
          />
          <article v-for="result in results" :key="result.citation_handle" class="rag-result" data-test="rag-result">
            <div class="result-meta">
              <span class="fg-badge fg-badge--confirmed">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
                  <path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z" />
                </svg>
                已确认 · {{ result.scope }}
              </span>
              <span>{{ result.source_type }} · 来源 {{ result.source_id }} · 修订 {{ result.revision }}</span>
              <span class="result-index">索引 {{ result.index_version ?? '—' }}</span>
            </div>
            <p>{{ result.text }}</p>
            <div class="result-footer">
              <code data-test="rag-citation">{{ result.citation_handle }}</code>
              <NButton
                size="tiny"
                secondary
                data-test="rag-save-candidate"
                @click="saveResult(result)"
              >
                保存为候选
              </NButton>
            </div>
          </article>
        </div>
      </NSpin>
    </template>

    <!-- 「保存」= 新建候选（编辑器提交 POST /memory-candidates），无直接发布 -->
    <MemoryEditorDialog v-model:show="editorOpen" :initial="editorInitial" />
  </section>
</template>

<style scoped>
.rag-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.rag-results {
  display: grid;
  gap: 10px;
  min-height: 60px;
}

.rag-result {
  padding: 15px;
  border: 1px solid var(--fg-line);
  border-left: 3px solid var(--fg-status-confirmed);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface-raised);
  box-shadow: var(--fg-shadow-card);
}

.result-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  color: var(--fg-ink-secondary);
  font-size: 11px;
}

.result-meta .fg-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.rag-result p {
  margin: 9px 0 5px;
  color: var(--fg-ink);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.result-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.rag-result code {
  color: var(--fg-ink-secondary);
  font-size: 10px;
  word-break: break-all;
}
</style>
