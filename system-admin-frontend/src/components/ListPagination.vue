<script setup lang="ts">
/**
 * 分页控件：页码/上下一页 + 每页条数。总页数由 total/pageSize 推导。
 */
import { computed } from 'vue'

const props = defineProps<{
  page: number
  pageSize: number
  total: number
}>()

const emit = defineEmits<{
  'update:page': [page: number]
}>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
</script>

<template>
  <div class="ag-toolbar" data-testid="pagination">
    <button
      type="button"
      class="ag-tag"
      :disabled="page <= 1"
      aria-label="上一页"
      @click="emit('update:page', page - 1)"
    >
      上一页
    </button>
    <span>第 {{ page }} / {{ totalPages }} 页（共 {{ total }} 条）</span>
    <button
      type="button"
      class="ag-tag"
      :disabled="page >= totalPages"
      aria-label="下一页"
      @click="emit('update:page', page + 1)"
    >
      下一页
    </button>
  </div>
</template>
