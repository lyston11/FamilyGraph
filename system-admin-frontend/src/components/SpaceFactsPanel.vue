<script setup lang="ts">
/**
 * 空间 confirmed 事实面板（敏感：space 访问会话；no-store）。
 * 只渲染事实类型/主体/来源枚举/版本；不渲染证据原文或描述正文。
 */
import { onMounted, ref, watch } from 'vue'
import { apiSpaceFacts } from '@/api/read'
import { AccessSessionInvalidError } from '@/api/client'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminFactOut, AdminPageOut } from '@/types/api'

const props = defineProps<{ spaceId: number }>()

const emit = defineEmits<{ unauthorized: [] }>()

const data = ref<AdminPageOut<AdminFactOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20

const FACT_LABELS: Record<string, string> = {
  biological_parent: '生物学父母',
  adoptive_parent: '养父母',
  step_parent: '继父母',
  guardian: '监护人',
  spouse: '配偶',
  partner: '伴侣',
  direct_sibling: '同胞兄弟姐妹',
}

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiSpaceFacts(props.spaceId, { page: page.value, pageSize })
    state.value = 'ready'
  } catch (error) {
    if (error instanceof AccessSessionInvalidError) {
      emit('unauthorized')
      return
    }
    state.value = 'error'
  }
}

watch([page], () => void load())
onMounted(load)
</script>

<template>
  <div data-testid="space-facts-panel">
    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无已确认事实" @retry="load" />
    <template v-else-if="data">
      <div v-if="data.items.length === 0">
        <PageState state="empty" empty-text="该空间暂无已确认事实" />
      </div>
      <template v-else>
        <div class="ag-table-wrap">
          <table class="ag-table" data-testid="facts-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>事实类型</th>
                <th>主体</th>
                <th>客体</th>
                <th>来源</th>
                <th>版本</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in data.items" :key="item.id">
                <td>{{ item.id }}</td>
                <td>{{ FACT_LABELS[item.fact_type] ?? item.fact_type }}</td>
                <td>{{ item.subject_name ?? `#${item.subject_user_id}` }}</td>
                <td>{{ item.object_name ?? `#${item.object_user_id}` }}</td>
                <td>{{ item.provenance }}</td>
                <td>r{{ item.revision }}</td>
                <td>{{ item.updated_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ListPagination
          :page="data.page"
          :page-size="data.page_size"
          :total="data.total"
          @update:page="page = $event"
        />
      </template>
    </template>
  </div>
</template>
