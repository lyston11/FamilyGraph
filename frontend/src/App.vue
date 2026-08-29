<script setup lang="ts">
// 根组件（design.md §2.2/§3.1）：全局 providers + 主题 token 注入 + 壳条件渲染。
// 过渡期：element-plus 全局注册仍在（P5-9 移除）；naive-ui 组件按需 import。
import { computed, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  NNotificationProvider,
  type GlobalThemeOverrides,
} from 'naive-ui'

import AssistantLauncher from '@/components/agent/AssistantLauncher.vue'
import AppShell from '@/components/shell/AppShell.vue'
import { themeOverrides } from '@/styles/naive-themes'
import { themeCssVars, themeTokens } from '@/styles/tokens'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const route = useRoute()

// 两主题均为浅色：不切换 naive 内置主题，仅注入 overrides
const naiveOverrides = computed<GlobalThemeOverrides>(() => themeOverrides[ui.theme])

// 沉浸页（login/onboarding/force-change-pin/identity-setup）不套应用壳
const isBlankChrome = computed(() => route.meta.chrome === 'blank')

// 主题 token 单一来源：L2 变量批量注入 documentElement，CSS 与 Naive UI overrides 同源
watchEffect(() => {
  const root = document.documentElement
  for (const [name, value] of themeCssVars(themeTokens[ui.theme])) {
    root.style.setProperty(name, value)
  }
})
</script>

<template>
  <NConfigProvider :theme-overrides="naiveOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <NNotificationProvider>
          <AppShell v-if="!isBlankChrome" />
          <RouterView v-else />
        </NNotificationProvider>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
  <!-- 悬浮 Assistant 保持全局（壳外，design.md §3.1），P4 迁移时视觉随主题 -->
  <AssistantLauncher />
</template>
