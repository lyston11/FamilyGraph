<script setup lang="ts">
/**
 * 未知路径 404：安全空态。
 * 不暴露任何"目标是否存在"的信息，也不提供跳转到家庭端的入口。
 */
import PageState from '@/components/PageState.vue'
</script>

<template>
  <div class="ag-card" data-testid="not-found">
    <PageState state="empty" empty-text="页面不存在或已被移除" />
    <div class="form-actions ag-actions-center">
      <RouterLink class="ag-tag" :to="{ name: 'overview' }">返回概览</RouterLink>
    </div>
  </div>
</template>
