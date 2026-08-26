<script setup lang="ts">
// 根组件：路由出口 + 全局悬浮 Assistant（V2.2；不绑死 FamilySpaceView）
import AssistantLauncher from '@/components/agent/AssistantLauncher.vue'
</script>

<template>
  <RouterView />
  <AssistantLauncher />
</template>
