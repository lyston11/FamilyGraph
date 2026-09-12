<script setup lang="ts">
// 记忆与知识页（09-06 重构）：与设置页同构的页面框架——
// 页头（eyebrow + 标题 + 说明，右侧 刷新/返回 动作）+ 左侧分区导航 + 右侧玻璃卡分区，
// 分区导航与分区卡由 MemoryManager 承载（五分区：待确认/私有/家庭/家族/检索）。
import { NButton } from 'naive-ui'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, RotateCw } from 'lucide-vue-next'

import MemoryManager from '@/components/memory/MemoryManager.vue'
import { useSpacesStore } from '@/stores/spaces'

const router = useRouter()
const spaces = useSpacesStore()
const managerRef = ref<InstanceType<typeof MemoryManager> | null>(null)

onMounted(() => {
  void spaces.load().catch(() => undefined)
})

function goHome(): void {
  void router.push({ name: 'home' })
}

/** 刷新走 MemoryManager.load（候选 + 私有 + 当前空间共享一并重读服务端） */
function refresh(): void {
  void managerRef.value?.load()
}
</script>

<template>
  <main class="memory-view" data-test="memory-view">
    <header class="memory-header">
      <div class="memory-heading">
        <p class="eyebrow">长期知识</p>
        <h1>记忆与知识</h1>
        <p class="header-meta">
          原始聊天不会自动进入检索。只有你确认的记忆，或明确授权的材料，才会成为可追溯的知识来源。
        </p>
      </div>
      <div class="memory-actions">
        <NButton quaternary data-test="memory-refresh" @click="refresh">
          <template #icon><RotateCw :size="16" aria-hidden="true" /></template>
          刷新
        </NButton>
        <NButton data-test="memory-back" @click="goHome">
          <template #icon><ArrowLeft :size="16" aria-hidden="true" /></template>
          返回我的家庭
        </NButton>
      </div>
    </header>
    <MemoryManager ref="managerRef" />
  </main>
</template>

<style scoped>
/* 与设置页同构的容器几何：1120px 居中 + 同套页头节奏 */
.memory-view {
  max-width: 1120px;
  margin: 0 auto;
  padding: 32px 20px 48px;
  box-sizing: border-box;
}

.memory-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 24px;
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--fg-accent);
  font-size: 12px;
  letter-spacing: 0;
}

h1 {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 30px;
  color: var(--fg-ink);
}

.header-meta {
  max-width: 560px;
  margin: 8px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.memory-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 10px;
}

@media (max-width: 480px) {
  .memory-view { padding: 22px 12px 36px; }
  .memory-header { flex-wrap: wrap; }
  h1 { font-size: 24px; }
}
</style>
