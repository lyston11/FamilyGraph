<script setup lang="ts">
/**
 * 列表/详情统一三态：loading / empty / error（带重试）。
 * 错误文案只显示统一安全信息，不展示 token、堆栈或请求正文。
 */
withDefaults(
  defineProps<{
    state: 'loading' | 'empty' | 'error'
    emptyText?: string
    errorText?: string
  }>(),
  {
    emptyText: '暂无数据',
    errorText: '加载失败，请稍后重试',
  },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="state === 'loading'" class="ag-state" role="status" data-testid="page-state-loading">
    加载中…
  </div>
  <div
    v-else-if="state === 'empty'"
    class="ag-state"
    role="status"
    data-testid="page-state-empty"
  >
    {{ emptyText }}
  </div>
  <div
    v-else
    class="ag-state ag-state-error"
    role="alert"
    data-testid="page-state-error"
  >
    <p class="ag-error-line">{{ errorText }}</p>
    <button type="button" class="ag-tag" data-testid="page-state-retry" @click="emit('retry')">
      重试
    </button>
  </div>
</template>
