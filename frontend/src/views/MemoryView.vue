<script setup lang="ts">
import { NButton } from 'naive-ui'
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'

import MemoryManager from '@/components/memory/MemoryManager.vue'
import { useSpacesStore } from '@/stores/spaces'

const router = useRouter()
const spaces = useSpacesStore()

onMounted(() => {
  void spaces.load().catch(() => undefined)
})

function goFamilySpace(): void {
  void router.push({ name: 'family-space' })
}

function goSettings(): void {
  void router.push({ name: 'settings' })
}
</script>

<template>
  <main class="memory-view">
    <!-- 09-06 视觉补齐：与家庭首页 family-space-hero 同套大卡设计语言 -->
    <article class="memory-hero">
      <header class="hero-head">
        <div class="hero-identity">
          <NButton quaternary size="small" data-test="memory-back" @click="goFamilySpace">
            ← 家庭空间
          </NButton>
          <p class="hero-kind">FamilyGraph / Knowledge</p>
          <h1 class="hero-title">记忆与知识</h1>
        </div>
        <NButton quaternary size="small" class="topbar-label" data-test="memory-settings" @click="goSettings">
          设置
        </NButton>
      </header>
      <div class="hero-body">
        <MemoryManager />
      </div>
    </article>
  </main>
</template>

<style scoped>
/* 页面底色随主题 token（body 点阵基座之上，仅保留主色柔光 wash） */
.memory-view {
  min-height: 100vh;
  padding: 24px clamp(16px, 5vw, 72px) 48px;
  background: radial-gradient(circle at 84% 0%, var(--fg-accent-soft), transparent 34%);
}

.memory-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  /* 宽度与家庭首页 household-card-view（1320px）保持一致 */
  max-width: 1320px;
  margin: 0 auto;
  padding: 36px 40px 24px;
  box-sizing: border-box;
  background:
    linear-gradient(125deg, color-mix(in srgb, var(--fg-ink) 9%, transparent), transparent 54%),
    var(--fg-glass-surface);
  border: 1px solid var(--fg-glass-border);
  border-top-color: color-mix(in srgb, var(--fg-ink) 30%, transparent);
  border-radius: 8px;
  backdrop-filter: blur(28px) saturate(115%);
  -webkit-backdrop-filter: blur(28px) saturate(115%);
  box-shadow:
    var(--fg-shadow-raised),
    0 2px 0 color-mix(in srgb, var(--fg-surface) 60%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 10%, transparent);
  transition: transform 350ms ease, box-shadow 350ms ease, border-color 350ms ease;
}

@supports not (backdrop-filter: blur(28px)) {
  .memory-hero { background: var(--fg-surface-raised); }
}

@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .memory-hero:hover {
    transform: translateY(-4px);
    border-top-color: color-mix(in srgb, var(--fg-ink) 45%, transparent);
    box-shadow: var(--fg-shadow-raised), 0 8px 0 -5px var(--fg-glass-border), inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 15%, transparent);
  }
}

.hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.hero-identity { min-width: 0; }

.hero-kind {
  margin: 0 0 10px;
  color: var(--fg-ink-faint);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.hero-title {
  margin: 0;
  color: var(--fg-ink);
  font-family: var(--fg-font-display);
  font-size: 32px;
  line-height: 1.4;
  font-weight: 600;
}

.hero-body { padding-top: 20px; }

.memory-view :deep(.memory-manager) {
  max-width: none;
}

@media (max-width: 600px) {
  .memory-hero { padding: 24px 20px 20px; }
  .topbar-label {
    display: none;
  }
}
</style>
