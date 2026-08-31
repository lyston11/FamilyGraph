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
</script>

<template>
  <main class="memory-view">
    <header class="topbar">
      <NButton quaternary data-test="memory-back" @click="router.push({ name: 'family-space' })">← 家庭空间</NButton>
      <span class="topbar-label">FamilyGraph / Knowledge</span>
      <NButton quaternary data-test="memory-settings" @click="router.push({ name: 'settings' })">设置</NButton>
    </header>
    <MemoryManager />
  </main>
</template>

<style scoped>
/* 页面底色随主题 token（body 点阵基座之上，仅保留主色柔光 wash） */
.memory-view {
  min-height: 100vh;
  padding: 24px clamp(16px, 5vw, 72px) 48px;
  background: radial-gradient(circle at 84% 0%, var(--fg-accent-soft), transparent 34%);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 920px;
  margin: 0 auto 26px;
}

.topbar-label {
  color: var(--fg-ink-faint);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.memory-view :deep(.memory-manager) {
  max-width: 920px;
  margin: 0 auto;
}

@media (max-width: 600px) {
  .topbar-label {
    display: none;
  }
}
</style>
