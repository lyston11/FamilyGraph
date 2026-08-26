<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import ActionCardItem from './ActionCardItem.vue'
import { useActionCardsStore } from '@/stores/actionCards'
import { useSpacesStore } from '@/stores/spaces'

/**
 * 空间管家建议 Inbox（V2.4 Block S3）：
 * - 入口按钮 + pending 数徽章；面板列表渲染 ActionCardItem（与 Assistant
 *   消息引用共用同一卡片组件）；
 * - 数据经 actionCards store 按当前空间加载；403 SPACE_FORBIDDEN_ACTOR /
 *   503（flag 关闭）时隐藏入口（降级不报错）；
 * - 切换空间关闭面板并重新加载；旧空间数据由 FamilySpaceView 的空间切换
 *   watch 调 resetForSpace 清理。
 */
const spaces = useSpacesStore()
const actionCards = useActionCardsStore()

const open = ref(false)

const spaceId = computed(() => spaces.currentSpaceId)
const partition = computed(() => (spaceId.value === null ? null : actionCards.partitionOf(spaceId.value)))
/** 未探测到失败前先展示入口，403/503 响应后降级隐藏 */
const hidden = computed(() => partition.value?.hidden ?? false)
const cards = computed(() => partition.value?.cards ?? [])
const loading = computed(() => partition.value?.loading ?? false)
const pendingCount = computed(() =>
  spaceId.value === null ? 0 : actionCards.pendingCountOf(spaceId.value),
)

async function load(): Promise<void> {
  if (spaceId.value === null) return
  await actionCards.loadForSpace(spaceId.value)
}

onMounted(() => void load())

watch(spaceId, () => {
  open.value = false
  void load()
})
</script>

<template>
  <template v-if="!hidden && spaceId !== null">
    <el-badge
      :value="pendingCount"
      :hidden="pendingCount === 0"
      type="primary"
      data-test="steward-inbox-badge"
    >
      <el-button size="small" data-test="steward-inbox-entry" @click="open = !open">
        管家建议
      </el-button>
    </el-badge>

    <section v-if="open" class="inbox-panel" data-test="steward-inbox-panel">
      <div v-if="loading && cards.length === 0" class="hint">加载中…</div>
      <el-empty
        v-else-if="cards.length === 0"
        description="管家暂时没有新的建议。完成建档与事实确认后，这里会出现可操作的推荐。"
        :image-size="72"
        data-test="steward-inbox-empty"
      />
      <ActionCardItem v-for="card in cards" v-else :key="card.id" :card="card" />
    </section>
  </template>
</template>

<style scoped>
.inbox-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
</style>
