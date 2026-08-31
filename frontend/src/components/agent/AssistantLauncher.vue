<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, ref, watch } from 'vue'

import { useAgentStore } from '@/stores/agent'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'

// P5 体积优化（P4 移交建议）：面板（NDrawer + PanelContent/消息/行动卡链）拆独立
// chunk 异步加载；launcher 按钮保持静态渲染、首帧立即可见（悬浮体验无回归）。
// chunk 在挂载后即开始预取，用户点击面板时通常已就绪。
const AssistantPanel = defineAsyncComponent(() => import('./AssistantPanel.vue'))

/**
 * AssistantLauncher（PRD AS-3）：全局悬浮入口，挂载于 App.vue。
 *
 * 显隐策略：
 * - 未登录 / 首登强制改 PIN / 确档向导中 → 整体隐藏（路由守卫本就拦截这些态）；
 * - 无可用空间 → 按钮可见但禁用（提示原因）；
 *
 * 空间切换联动（design.md）：currentSpaceId 变化时 resetForSpace 清空旧 scope
 * （关流、删分区与草稿），再 ensureSpace 装载新 scope 的会话列表。
 */
const auth = useAuthStore()
const spaces = useSpacesStore()
const agent = useAgentStore()

const open = ref(false)
const launcherEl = ref<HTMLButtonElement | null>(null)

const hidden = computed(
  () =>
    !auth.isLoggedIn ||
    auth.mustChangePin ||
    auth.user?.profile_status === 'provisional',
)

const noSpace = computed(() => spaces.currentSpace === null)

watch(
  [() => spaces.currentSpaceId, hidden],
  ([spaceId, isHidden], previous) => {
    const [previousSpaceId] = previous ?? [null]
    if (isHidden) {
      open.value = false
      return
    }
    // 跨 scope 对抗：旧空间数据立即清除，UI 无残留消息、旧流强制关闭
    if (
      typeof previousSpaceId === 'number' &&
      typeof spaceId === 'number' &&
      previousSpaceId !== spaceId
    ) {
      agent.resetForSpace(previousSpaceId)
    }
    if (typeof spaceId === 'number') void agent.ensureSpace(spaceId)
  },
  { immediate: true },
)

// 关闭后焦点回 launcher（a11y：focus 回收）
watch(open, (value) => {
  if (!value) {
    void nextTick(() => launcherEl.value?.focus())
  }
})
</script>

<template>
  <div v-if="!hidden" class="assistant-launcher">
    <AssistantPanel v-model:open="open" />
    <button
      ref="launcherEl"
      type="button"
      class="launcher-btn"
      :disabled="noSpace"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-label="open ? '关闭家庭助手' : '打开家庭助手'"
      :title="noSpace ? '暂无可用家庭空间' : '家庭助手'"
      data-test="assistant-launcher"
      @click="open = !open"
    >
      <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
        <path
          fill="currentColor"
          d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM7 9h10v2H7V9zm6 5H7v-2h6v2zm4-6H7V6h10v2z"
        />
      </svg>
    </button>
  </div>
</template>

<style scoped>
.assistant-launcher {
  position: fixed;
  right: calc(20px + env(safe-area-inset-right));
  bottom: calc(20px + env(safe-area-inset-bottom));
  /* 低于 n-drawer/n-modal 浮层（naive 浮层 zIndex ≥ 2000），高于壳导航（100） */
  z-index: 1500;
}

/* 朱砂/青蓝圆形按钮：主色实底 + 主题浮起阴影 */
.launcher-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  background: var(--fg-accent);
  color: var(--fg-accent-ink);
  box-shadow: var(--fg-shadow-raised);
  transition: transform 0.15s ease;
}

.launcher-btn:hover:not(:disabled) {
  transform: scale(1.06);
}

.launcher-btn:focus-visible {
  outline: 2px solid var(--fg-accent);
  outline-offset: 3px;
}

.launcher-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (prefers-reduced-motion: reduce) {
  .launcher-btn {
    transition: none;
  }
}
</style>
