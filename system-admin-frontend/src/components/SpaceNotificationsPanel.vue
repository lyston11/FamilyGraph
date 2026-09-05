<script setup lang="ts">
/**
 * 空间运营通知面板（普通读取，无需票据）。
 */
import { onMounted, ref, watch } from 'vue'
import { apiOperationsNotifications } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminNotificationOut, AdminPageOut } from '@/types/api'

const props = defineProps<{ spaceId: number }>()

const data = ref<AdminPageOut<AdminNotificationOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20
const readFilter = ref<'' | 'read' | 'unread'>('')

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiOperationsNotifications({
      page: page.value,
      pageSize,
      spaceId: props.spaceId,
      read: readFilter.value === '' ? null : readFilter.value === 'read',
    })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

watch([page, readFilter], () => void load())
onMounted(load)
</script>

<template>
  <div data-testid="space-notifications-panel">
    <div class="ag-toolbar">
      <label class="ag-filter-label">
        读取状态
        <select v-model="readFilter" aria-label="按读取状态筛选" data-testid="notifications-read">
          <option value="">全部</option>
          <option value="read">已读</option>
          <option value="unread">未读</option>
        </select>
      </label>
    </div>
    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无通知" @retry="load" />
    <template v-else-if="data">
      <div v-if="data.items.length === 0">
        <PageState state="empty" empty-text="没有匹配的通知" />
      </div>
      <template v-else>
        <div class="ag-cards">
          <div v-for="item in data.items" :key="item.id" class="ag-card-row">
            <div class="ag-card-row-title">{{ item.title }}</div>
            <div v-if="item.summary" class="ag-card-row-meta">{{ item.summary }}</div>
            <div class="ag-card-row-meta">
              通知 #{{ item.id }} · 收件账号 #{{ item.recipient_account_id }} ·
              {{ item.read_at ? `已读于 ${item.read_at}` : '未读' }}
            </div>
          </div>
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
