<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { search, type SearchHit } from '@/api/stats'

/**
 * 全局搜索框（m3d）：名字/称谓前缀匹配；结果=可见性基线内的摘要。
 * 点击 full 级跳转档案列表页；summary 级提示遮罩态。
 */
const router = useRouter()
const keyword = ref('')
const hits = ref<SearchHit[]>([])
const searching = ref(false)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

function onInput() {
  if (debounceTimer) clearTimeout(debounceTimer)
  const q = keyword.value.trim()
  if (!q) {
    hits.value = []
    return
  }
  debounceTimer = setTimeout(async () => {
    searching.value = true
    try {
      hits.value = await search(q)
    } catch {
      hits.value = []
    } finally {
      searching.value = false
    }
  }, 250)
}

function pick(hit: SearchHit) {
  keyword.value = ''
  hits.value = []
  // v1 统一跳档案列表页查看；详情遮罩态由后端矩阵保证
  void router.push({ name: 'home', query: { highlight: String(hit.id) } })
}
</script>

<template>
  <div class="global-search" data-test="global-search">
    <input
      v-model="keyword"
      class="search-input"
      type="search"
      placeholder="搜索家人名字或称谓…"
      aria-label="搜索家人"
      data-test="search-input"
      @input="onInput"
    />
    <ul v-if="hits.length > 0 && keyword" class="results" data-test="search-results">
      <li v-for="hit in hits" :key="hit.id" data-test="search-hit" @click="pick(hit)">
        <span class="name">{{ hit.name }}</span>
        <span class="level">{{ hit.level === 'full' ? '可查看详情' : '仅摘要可见' }}</span>
      </li>
    </ul>
    <p v-else-if="keyword && !searching" class="no-hit" data-test="search-empty">未找到相关家人</p>
  </div>
</template>

<style scoped>
.global-search {
  position: relative;
  width: 240px;
}

.search-input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color);
}

.results {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  box-shadow: var(--el-box-shadow-light);
  list-style: none;
  margin: 0;
  padding: 4px 0;
  z-index: 100;
}

.results li {
  display: flex;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
}

.results li:hover {
  background: var(--el-fill-color-light);
}

.level {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.no-hit {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 12px;
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  z-index: 100;
}
</style>
