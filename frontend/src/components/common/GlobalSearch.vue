<script setup lang="ts">
import { NInput } from 'naive-ui'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { search, type SearchHit } from '@/api/stats'

/**
 * 全局搜索框（m3d / P4-4）：名字/称谓前缀匹配；结果=可见性基线内的摘要。
 * 点击 full 级跳转档案列表页；summary 级提示遮罩态。
 * 下拉沿用页内绝对定位（无 teleport）：壳导航为 sticky z=100 的堆叠上下文，
 * 下拉在其内部 z=20，天然压过页面内容且不会被抽屉/模态（z≥2000）误压——
 * teleport 与 z-index 回归点已在此固化。
 * 键盘可达（a11y 基线）：Esc 收起、方向键移动高亮、回车选择；点击外部收起。
 */
const router = useRouter()
const keyword = ref('')
const hits = ref<SearchHit[]>([])
const searching = ref(false)
const open = ref(false)
const activeIndex = ref(-1)

const rootEl = ref<HTMLDivElement | null>(null)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

function closeResults(): void {
  open.value = false
  activeIndex.value = -1
}

function onInput(value: string) {
  keyword.value = value
  if (debounceTimer) clearTimeout(debounceTimer)
  const q = keyword.value.trim()
  if (!q) {
    hits.value = []
    closeResults()
    return
  }
  debounceTimer = setTimeout(async () => {
    searching.value = true
    try {
      hits.value = await search(q)
      activeIndex.value = -1
      open.value = true
    } catch {
      hits.value = []
    } finally {
      searching.value = false
    }
  }, 250)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    if (open.value) {
      event.stopPropagation()
      closeResults()
    }
    return
  }
  if (!open.value || hits.value.length === 0) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = activeIndex.value >= hits.value.length - 1 ? 0 : activeIndex.value + 1
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = activeIndex.value <= 0 ? hits.value.length - 1 : activeIndex.value - 1
    return
  }
  if (event.key === 'Enter') {
    const hit = hits.value[activeIndex.value] ?? hits.value[0]
    if (hit) {
      event.preventDefault()
      pick(hit)
    }
  }
}

function onDocumentPointerdown(event: PointerEvent): void {
  const root = rootEl.value
  if (root === null) return
  if (!(event.target instanceof Node)) return
  if (!root.contains(event.target)) closeResults()
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerdown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerdown)
  if (debounceTimer) clearTimeout(debounceTimer)
})

function pick(hit: SearchHit) {
  keyword.value = ''
  hits.value = []
  closeResults()
  // v1 统一跳档案列表页查看；详情遮罩态由后端矩阵保证
  void router.push({ name: 'home', query: { highlight: String(hit.id) } })
}
</script>

<template>
  <div ref="rootEl" class="global-search" data-test="global-search" @keydown="onKeydown">
    <NInput
      :value="keyword"
      clearable
      placeholder="搜索家人名字或称谓…"
      data-test="search-input"
      aria-label="搜索家人"
      size="small"
      @update:value="onInput"
    />
    <ul
      v-if="open && hits.length > 0"
      class="results"
      role="listbox"
      data-test="search-results"
    >
      <li
        v-for="(hit, index) in hits"
        :key="hit.id"
        role="option"
        :aria-selected="index === activeIndex"
        :class="{ active: index === activeIndex }"
        data-test="search-hit"
        @mouseenter="activeIndex = index"
        @click="pick(hit)"
      >
        <span class="name">{{ hit.name }}</span>
        <span class="level">{{ hit.level === 'full' ? '可查看详情' : '仅摘要可见' }}</span>
      </li>
    </ul>
    <p v-else-if="open && keyword && !searching" class="no-hit" data-test="search-empty">
      未找到相关家人
    </p>
  </div>
</template>

<style scoped>
.global-search {
  position: relative;
  width: 100%;
  /* 壳内块级布局吃满 480px 槽位；作为 flex 子项（如画布页旧顶栏）时取 240px 基准可收缩 */
  flex: 0 1 240px;
  min-width: 160px;
}

/* 下拉浮层：raised 面板 + 主题浮起阴影（随主题 token，teleport 与否不丢主题） */
.results {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  box-shadow: var(--fg-shadow-raised);
  list-style: none;
  margin: 0;
  padding: 4px 0;
  /* 壳导航（z=100 sticky）已建立堆叠上下文，此处局部层级压过页面内容即可 */
  z-index: 20;
}

.results li {
  display: flex;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  color: var(--fg-ink);
}

.results li:hover,
.results li.active {
  background: var(--fg-surface-sunken);
}

.level {
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.no-hit {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  background: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  padding: 8px 12px;
  margin: 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  z-index: 20;
}
</style>
