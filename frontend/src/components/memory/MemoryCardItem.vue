<script setup lang="ts">
// 正式记忆卡（private/household/lineage 标签共用）：
// scope、敏感等级、修订、保留期限以 icon+文字呈现（不只靠颜色）；
// 撤销/删除只 emit，由容器经 memory store 写服务端并重读。
import { NButton } from 'naive-ui'
import {
  MEMORY_SCOPE_LABELS,
  MEMORY_SENSITIVITY_LABELS,
  type Memory,
  type MemoryScopeKind,
  type MemorySensitivity,
} from '@/types/memory'

defineProps<{ item: Memory }>()
defineEmits<{ (event: 'revoke'): void; (event: 'remove'): void }>()

function formatRetention(value: string | null): string {
  return value ? `保留至 ${value.replace('T', ' ').slice(0, 16)}` : '长期保留'
}

function scopeLabel(item: Memory): string {
  if (item.scope === 'private') return MEMORY_SCOPE_LABELS.private
  return MEMORY_SCOPE_LABELS[item.scope as MemoryScopeKind]
}

/** 敏感等级徽章阶（design.md §3.4）：normal=confirmed / 敏感系=proposed / 高危=disputed */
function sensitivityBadge(sensitivity: MemorySensitivity): string {
  if (sensitivity === 'normal') return 'fg-badge--confirmed'
  if (sensitivity === 'high' || sensitivity === 'local_required') return 'fg-badge--disputed'
  return 'fg-badge--proposed'
}

/** scope 徽章：正式记忆视觉状态与候选（proposed 左缘线）、检索结果（confirmed）分离 */
function scopeBadgeClass(item: Memory): string {
  if (item.scope === 'private') return 'fg-badge--neutral'
  return item.scope === 'household' ? 'fg-badge--confirmed' : 'fg-badge--accent'
}
</script>

<template>
  <article class="memory-card" data-test="memory-card">
    <div class="memory-topline">
      <div class="memory-main">
        <span class="memory-title">{{ item.content }}</span>
        <p class="memory-scope">修订 {{ item.revision }} · {{ formatRetention(item.retention_until) }}</p>
      </div>
      <span class="fg-badge" :class="sensitivityBadge(item.sensitivity)">
        敏感等级：{{ MEMORY_SENSITIVITY_LABELS[item.sensitivity] }}
      </span>
    </div>
    <blockquote>原话：“{{ item.raw_quote }}”</blockquote>
    <div class="memory-meta">
      <span class="fg-badge memory-scope-badge" :class="scopeBadgeClass(item)">
        <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
          <path d="M12 1 3 5v6c0 5.6 3.8 10.4 9 12 5.2-1.6 9-6.4 9-12V5z" />
        </svg>
        范围：{{ scopeLabel(item) }}{{ item.space_id === null ? '' : ` · #${item.space_id}` }}
      </span>
      <span>用途：{{ item.purpose }}</span>
    </div>
    <div class="memory-actions">
      <NButton size="small" secondary data-test="revoke-memory" @click="$emit('revoke')">撤销检索</NButton>
      <NButton size="small" type="error" secondary data-test="delete-memory" @click="$emit('remove')">删除</NButton>
    </div>
  </article>
</template>

<style scoped>
.memory-card {
  padding: 18px 20px;
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  background: var(--fg-glass-surface);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  box-shadow:
    0 4px 20px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 20%, transparent);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.memory-card:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--fg-accent) 30%, var(--fg-glass-border));
  box-shadow:
    0 8px 30px color-mix(in srgb, var(--fg-ink) 10%, transparent),
    0 0 16px var(--fg-glass-glow);
}

.memory-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.memory-main {
  min-width: 0;
}

.memory-topline .fg-badge {
  flex-shrink: 0;
}

.memory-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.memory-scope {
  margin: 3px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 12px;
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

.memory-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 14px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.memory-scope-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.memory-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}

@media (max-width: 600px) {
  .memory-topline {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }

  .memory-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
