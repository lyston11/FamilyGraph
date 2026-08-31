<script setup lang="ts">
import { NDrawer } from 'naive-ui'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import PanelContent from './PanelContent.vue'

/**
 * AssistantPanel（PRD AS-3 / AC-AS1）：
 * - 桌面 ≥768px：n-drawer 抽屉，宽度可拖拽/键盘调整（自绘 resize 手柄保留键盘路径）；
 * - 移动端 <768px：全屏面板（不复用固定 420px 的 ProfileDrawer 方案）；
 * - 两种容器共享 PanelContent 内容层（人格标识位 / ScopeBanner / 会话层都在其中）；
 *   打开时焦点入面板、Esc 关闭、关闭后由宿主把焦点还给 launcher。
 * - naive 浮层 zIndex ≥2000（vdirs zindexable 自增），压过 launcher 1500 与壳导航 100。
 */
// open 仅模板使用；焦点管理在 PanelContent 内部处理
defineProps<{ open: boolean }>()

const emit = defineEmits<{ 'update:open': [value: boolean] }>()

const MOBILE_QUERY = '(max-width: 768px)'
const WIDTH_MIN = 360
const WIDTH_MAX = 900
const WIDTH_STEP = 24

const isMobile = ref(false)
const drawerWidth = ref(420)
const contentRef = ref<InstanceType<typeof PanelContent> | null>(null)

let mediaQuery: MediaQueryList | null = null

function onMediaChange(event: MediaQueryListEvent): void {
  isMobile.value = event.matches
}

onMounted(() => {
  mediaQuery = window.matchMedia(MOBILE_QUERY)
  isMobile.value = mediaQuery.matches
  mediaQuery.addEventListener('change', onMediaChange)
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', onMediaChange)
})

function close(): void {
  emit('update:open', false)
}

// ---- 桌面抽屉宽度调整（指针拖拽 + 键盘）----

function clampWidth(width: number): number {
  const max = Math.min(WIDTH_MAX, window.innerWidth - 80)
  return Math.min(max, Math.max(WIDTH_MIN, width))
}

function onResizeKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    drawerWidth.value = clampWidth(drawerWidth.value - WIDTH_STEP)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    drawerWidth.value = clampWidth(drawerWidth.value + WIDTH_STEP)
  }
}

function onResizePointerdown(event: PointerEvent): void {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = drawerWidth.value
  const onMove = (moveEvent: PointerEvent): void => {
    drawerWidth.value = clampWidth(startWidth + (startX - moveEvent.clientX))
  }
  const onUp = (): void => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}
</script>

<template>
  <!-- 桌面：可调宽抽屉 -->
  <NDrawer
    v-if="!isMobile"
    :show="open"
    placement="right"
    :width="drawerWidth"
    :auto-focus="false"
    :content-style="{ padding: '0', height: '100%' }"
    data-test="assistant-panel"
    @update:show="(value: boolean) => !value && close()"
  >
    <div
      class="resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整面板宽度"
      tabindex="0"
      data-test="panel-resize-handle"
      @pointerdown="onResizePointerdown"
      @keydown="onResizeKeydown"
    ></div>
    <PanelContent ref="contentRef" :open="open" @escape="close" @close="close" />
  </NDrawer>

  <!-- 移动端：全屏面板 -->
  <Teleport to="body">
    <Transition name="assistant-fade">
      <div v-if="open && isMobile" class="mobile-overlay" data-test="assistant-panel-mobile">
        <PanelContent ref="contentRef" :open="open" @escape="close" @close="close" />
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.resize-handle {
  position: absolute;
  top: 0;
  left: 0;
  width: 6px;
  height: 100%;
  cursor: col-resize;
  background: transparent;
  z-index: 2;
}

.resize-handle:hover,
.resize-handle:focus-visible {
  background: var(--fg-accent-soft);
}

.mobile-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  flex-direction: column;
  background: var(--fg-surface-raised);
}

/* reduced-motion：关闭面板过渡动画 */
@media (prefers-reduced-motion: reduce) {
  .assistant-fade-enter-active,
  .assistant-fade-leave-active {
    transition: none !important;
  }

  .mobile-overlay :deep(*) {
    animation-duration: 0s !important;
    transition-duration: 0s !important;
  }
}

.assistant-fade-enter-active,
.assistant-fade-leave-active {
  transition: opacity 0.2s ease;
}

.assistant-fade-enter-from,
.assistant-fade-leave-to {
  opacity: 0;
}
</style>
