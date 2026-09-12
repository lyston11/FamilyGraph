<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, zhCN, dateZhCN } from 'naive-ui'

import AdminShell from '@/components/AdminShell.vue'

const route = useRoute()

// 登录与首次改密保持沉浸式页面；其余受保护路由进入统一后台壳层。
// 壳层内部承载 RouterView，避免认证页出现导航或短暂闪烁。
const showAdminShell = computed(
  () => route.meta.requiresAuth === true && route.name !== 'force-change-password',
)
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN">
    <AdminShell v-if="showAdminShell" />
    <RouterView v-else />
  </NConfigProvider>
</template>
