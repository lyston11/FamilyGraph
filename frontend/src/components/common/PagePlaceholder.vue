<script setup lang="ts">
// 统一状态面板（design.md §5.4「统一状态面板」的最小形态）：
// 阶段性占位页共用——只说明页面建设中，不渲染任何假数据、不引入领域 store。
defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <section class="page-placeholder" aria-live="polite">
    <div class="placeholder-card">
      <svg
        class="glyph"
        viewBox="0 0 48 48"
        width="40"
        height="40"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <rect x="8" y="10" width="32" height="28" rx="3" />
        <path d="M8 18h32" />
        <path d="M16 26h16" />
        <path d="M16 31h10" />
      </svg>
      <h1 class="title">{{ title }}</h1>
      <p class="description">{{ description }}</p>
    </div>
  </section>
</template>

<style scoped>
.page-placeholder {
  display: grid;
  place-items: center;
  min-height: 60vh;
  padding: 24px 16px;
  box-sizing: border-box;
}

.placeholder-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  width: min(420px, 100%);
  padding: 32px 24px;
  box-sizing: border-box;
  text-align: center;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
}

.glyph {
  color: var(--fg-ink-faint);
}

.title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 18px;
  font-weight: 700;
  color: var(--fg-ink);
}

.description {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
}
</style>
