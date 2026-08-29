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

/** 域名徽章：仅解析展示 URL 主机名（纯字符串，无请求、无原始正文渲染） */
function domainOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
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
          <!-- 来源徽章：域名 + 用途标签（外部=未经家谱确认 → 未确认警示阶） -->
          <span class="fg-badge fg-badge--neutral" data-test="web-citation-domain">
            {{ domainOf(citation.url) }}
          </span>
          <span class="fg-badge fg-badge--proposed" data-test="web-citation-external">外部</span>
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
  border-top: 1px solid var(--fg-line);
}

.heading {
  margin: 0 0 6px;
  color: var(--fg-ink-secondary);
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
  color: var(--fg-ink-secondary);
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
  color: var(--fg-accent);
  text-decoration: none;
  font-weight: 600;
  word-break: break-all;
}

.title:hover {
  text-decoration: underline;
}

.excerpt {
  margin: 3px 0;
  color: var(--fg-ink);
  white-space: pre-wrap;
  word-break: break-word;
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.url {
  color: var(--fg-ink-secondary);
  word-break: break-all;
}

.fetched {
  color: var(--fg-ink-secondary);
  white-space: nowrap;
}
</style>
