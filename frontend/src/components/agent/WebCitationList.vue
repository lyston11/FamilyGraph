<script setup lang="ts">
import type { WebCitation } from '@/types/agent'

withDefaults(
  defineProps<{
    citations: WebCitation[]
    compact?: boolean
  }>(),
  { compact: false },
)

function formatFetchedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
</script>

<template>
  <section
    v-if="citations.length > 0"
    class="web-citations"
    data-test="web-citation-list"
    aria-label="联网引用来源"
  >
    <h4 class="heading">联网来源（外部资料，未经家谱确认）</h4>
    <ol class="list">
      <li
        v-for="(citation, index) in citations"
        :key="`${citation.url}-${index}`"
        class="citation"
        data-test="web-citation-item"
      >
        <div class="citation-meta">
          <a :href="citation.url" target="_blank" rel="noopener noreferrer" class="title">
            {{ citation.title }}
          </a>
          <el-tag size="small" type="warning">外部</el-tag>
        </div>
        <p v-if="!compact && citation.excerpt" class="excerpt">{{ citation.excerpt }}</p>
        <div class="meta">
          <span class="url">{{ citation.url }}</span>
          <span class="fetched">抓取于 {{ formatFetchedAt(citation.fetched_at) }}</span>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.web-citations {
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

.title {
  color: var(--el-color-primary);
  text-decoration: none;
  font-weight: 600;
  word-break: break-all;
}

.title:hover {
  text-decoration: underline;
}

.excerpt {
  margin: 3px 0;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.url {
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

.fetched {
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}
</style>