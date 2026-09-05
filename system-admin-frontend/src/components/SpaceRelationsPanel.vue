<script setup lang="ts">
/**
 * 空间关系边面板（敏感：space 访问会话；no-store）。
 * 只渲染结构化边（方向/状态/脱敏称谓）；不渲染证据原文、RawRelationInput、
 * private note 或消息。
 */
import { onMounted, ref, watch } from 'vue'
import { apiSpaceRelations } from '@/api/read'
import { AccessSessionInvalidError } from '@/api/client'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminPageOut, AdminRelationOut } from '@/types/api'

const props = defineProps<{ spaceId: number }>()

const emit = defineEmits<{ unauthorized: [] }>()

const data = ref<AdminPageOut<AdminRelationOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20
const statusFilter = ref<'' | 'pending' | 'active' | 'rejected' | 'cancelled' | 'revoked'>('')

const DIR_LABELS: Record<string, string> = {
  elder: '长辈（尊→卑）',
  younger: '晚辈（卑→尊）',
  peer: '同辈',
  spouse: '配偶',
}

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiSpaceRelations(props.spaceId, {
      page: page.value,
      pageSize,
      status: statusFilter.value || null,
    })
    state.value = 'ready'
  } catch (error) {
    if (error instanceof AccessSessionInvalidError) {
      emit('unauthorized')
      return
    }
    state.value = 'error'
  }
}

watch([page, statusFilter], () => void load())
onMounted(load)
</script>

<template>
  <div data-testid="space-relations-panel">
    <div class="ag-toolbar">
      <label class="ag-filter-label">
        状态
        <select v-model="statusFilter" aria-label="按关系状态筛选" data-testid="relations-status">
          <option value="">全部</option>
          <option value="pending">待确认</option>
          <option value="active">生效</option>
          <option value="rejected">已拒绝</option>
          <option value="cancelled">已取消</option>
          <option value="revoked">已解除</option>
        </select>
      </label>
    </div>
    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无关系边" @retry="load" />
    <template v-else-if="data">
      <div v-if="data.items.length === 0">
        <PageState state="empty" empty-text="没有匹配的关系边" />
      </div>
      <template v-else>
        <div class="ag-table-wrap">
          <table class="ag-table" data-testid="relations-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>方向</th>
                <th>发起方</th>
                <th>接收方</th>
                <th>称谓（脱敏）</th>
                <th>状态</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in data.items" :key="item.id">
                <td>{{ item.id }}</td>
                <td>{{ DIR_LABELS[item.dir_class] ?? item.dir_class }}</td>
                <td>{{ item.from_user_name ?? `#${item.from_user_id}` }}</td>
                <td>{{ item.to_user_name ?? `#${item.to_user_id}` }}</td>
                <td>{{ item.label_safe ?? '-' }}</td>
                <td>{{ item.status }}</td>
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
