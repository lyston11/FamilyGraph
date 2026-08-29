<script setup lang="ts">
import { computed } from 'vue'

/**
 * 遮罩字段渲染（architecture §6）：{__masked__: true} → 锁样式；
 * 否则透传渲染值。所有可见性敏感展示统一走此组件（单点实现）。
 *
 * 锁样式双主题（design.md §3.4「masked 字段 = 统一锁形章占位」）：
 * - 纸墨：封条/印章质感——虚线封条框 + 锁形小方章（seal 色系，仅边框与文字吃
 *   --fg-status-masked，不加柔底：seal 文字对纸面须 ≥4.5:1，实测 4.93+）；
 * - 清雅：干净锁形 chip——细线胶囊 + 锁形图标（slate 仅作图形 ≥3:1，文字走
 *   ink-secondary 保证小字 AA）。
 */
const props = defineProps<{ value: unknown }>()

const masked = computed(
  () =>
    typeof props.value === "object" &&
    props.value !== null &&
    "__masked__" in (props.value as Record<string, unknown>),
)

const text = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return "不详"
  if (typeof v === "string") return v
  if (typeof v === "object") {
    // 结构化日期：优先人读文本，其次 mirror，最后 ISO
    const d = v as { date?: string | null; mirror_date?: string | null }
    return d.date ?? d.mirror_date ?? "不详"
  }
  return String(v)
})
</script>

<template>
  <span v-if="masked" class="masked-field" data-test="masked-field">
    <span class="lock-stamp" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor">
        <path
          d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5Zm-3 8V7a3 3 0 1 1 6 0v3H9Z"
        />
      </svg>
    </span>
    <span class="masked-text">已隐藏</span>
  </span>
  <span v-else data-test="unmasked-value">{{ text }}</span>
</template>

<style scoped>
.masked-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px 1px 4px;
  vertical-align: baseline;
  white-space: nowrap;
}

.lock-stamp {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  color: var(--fg-status-masked);
}

.masked-text {
  font-size: 12px;
  line-height: 1.6;
  letter-spacing: 0.08em;
}

/* 纸墨：封条/印章——虚线封条框 + 实线小方章，印章用宋体感边角（直角） */
[data-theme='paper'] .masked-field {
  border: 1px dashed color-mix(in srgb, var(--fg-status-masked) 60%, transparent);
  border-radius: 2px;
  background-color: transparent;
}

[data-theme='paper'] .masked-field .lock-stamp {
  border: 1px solid var(--fg-status-masked);
  border-radius: 2px;
}

[data-theme='paper'] .masked-field .masked-text {
  color: var(--fg-status-masked);
}

/* 清雅：干净锁形 chip——细线胶囊 + 锁形图标，无印章质感 */
[data-theme='modern'] .masked-field {
  border: 1px solid var(--fg-line-strong);
  border-radius: 999px;
  background-color: var(--fg-surface-sunken);
  padding: 1px 10px 1px 5px;
}

[data-theme='modern'] .masked-field .lock-stamp {
  border: none;
  border-radius: 0;
}

[data-theme='modern'] .masked-field .masked-text {
  color: var(--fg-ink-secondary);
}
</style>
