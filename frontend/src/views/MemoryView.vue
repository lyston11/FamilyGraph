<script setup lang="ts">
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
      <el-button text data-test="memory-back" @click="router.push('/')">← 家庭空间</el-button>
      <span class="topbar-label">FamilyGraph / Knowledge</span>
      <el-button text data-test="memory-settings" @click="router.push('/settings')">设置</el-button>
    </header>
    <MemoryManager />
  </main>
</template>

<style scoped>
.memory-view {
  min-height: 100vh;
  padding: 24px clamp(16px, 5vw, 72px) 48px;
  background:
    radial-gradient(circle at 84% 0%, rgb(196 229 213 / 48%), transparent 34%),
    #f7fbf8;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 920px;
  margin: 0 auto 26px;
}

.topbar-label {
  color: #78918b;
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
