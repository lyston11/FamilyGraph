<script setup lang="ts">
// 根组件（design.md §2.2/§3.1）：全局 providers + 主题 token 注入 + 壳条件渲染。
// P5 收尾：旧组件库已全量移除，naive-ui 为唯一组件库（组件按需 import）。
// 09-04：家庭端是唯一产品面，后台在独立前端应用，本组件无任何后台壳分支。
import { computed, defineAsyncComponent, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import {
  NConfigProvider,
  darkTheme,
  NDialogProvider,
  NMessageProvider,
  NNotificationProvider,
  type GlobalThemeOverrides,
} from 'naive-ui'

import AssistantLauncher from '@/components/agent/AssistantLauncher.vue'
import { themeOverrides } from '@/styles/naive-themes'
import { themeCssVars, themeTokens } from '@/styles/tokens'
import { useUiStore } from '@/stores/ui'

// 应用壳按需加载（09-11 R6 性能治理）：blank chrome（登录/引导/404）不渲染壳，
// 壳及其专属依赖不再静态进入首屏主 chunk；与 AssistantPanel 同一 async 模式。
const AppShell = defineAsyncComponent(() => import('@/components/shell/AppShell.vue'))

const ui = useUiStore()
const route = useRoute()

// 两套配色共用 Naive 深色基座，所有表面和语义色从 token 派生。
const naiveOverrides = computed<GlobalThemeOverrides>(() => themeOverrides[ui.theme])

// 沉浸页（login/onboarding/force-change-pin/identity-setup/404）不套应用壳
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
  <NConfigProvider :theme="darkTheme" :theme-overrides="naiveOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <NNotificationProvider>
          <AppShell v-if="!isBlankChrome" />
          <RouterView v-else />
        </NNotificationProvider>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
  <!-- 悬浮 Assistant 保持全局（壳外，design.md §3.1）；面板内容经 defineAsyncComponent 懒加载 -->
  <AssistantLauncher v-if="!isBlankChrome" />
</template>
