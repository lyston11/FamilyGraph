<script setup lang="ts">
import type { MemoryCitation } from '@/types/memory'

withDefaults(
  defineProps<{
    citations: MemoryCitation[]
    compact?: boolean
  }>(),
  { compact: false },
)

function sourceLabel(sourceType: string): string {
  const labels: Record<string, string> = {
    memory: '确认记忆',
    family_story: '家族故事',
    authorized_document: '授权文档',
    profile: '本人简介',
    public_kinship: '公共称谓知识',
  }
  return labels[sourceType] ?? '授权知识'
}
</script>

<template>
  <section v-if="citations.length > 0" class="citations" data-test="citation-list" aria-label="引用来源">
    <h4 class="heading">引用来源</h4>
    <ol class="list">
      <li v-for="citation in citations" :key="citation.citation_handle" class="citation" data-test="citation-item">
        <div class="citation-meta">
          <strong>{{ sourceLabel(citation.source_type) }}</strong>
          <span>来源 {{ citation.source_id }}</span>
          <span>修订 {{ citation.revision }}</span>
          <el-tag size="small" type="info">{{ citation.scope }}</el-tag>
        </div>
        <p v-if="!compact && citation.text" class="quote">{{ citation.text }}</p>
        <code class="handle">{{ citation.citation_handle }}</code>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.citations {
  width: 100%;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.heading {
  margin: 0 0 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding-left: 22px;
}

.citation {
  min-width: 0;
  color: var(--el-text-color-secondary);
  font-size: 11px;
  line-height: 1.45;
}

.citation-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.citation-meta strong {
  color: var(--el-text-color-regular);
}

.quote {
  margin: 3px 0;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.handle {
  color: var(--el-text-color-secondary);
  word-break: break-all;
}
</style>
