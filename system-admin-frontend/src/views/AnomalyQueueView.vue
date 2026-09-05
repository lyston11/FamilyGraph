<script setup lang="ts">
/**
 * 异常队列：无管理员/双管理员/管理员锁定或删除等异常空间只读列表。
 * 不提供自动修复（PRD FE-F3）；提供关联钻取到空间详情。
 */
import { onMounted, ref, watch } from 'vue'
import { apiOperationsQueue } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminOperationsQueueItemOut, AdminPageOut } from '@/types/api'

const data = ref<AdminPageOut<AdminOperationsQueueItemOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiOperationsQueue({
      page: page.value,
      pageSize,
      kind: 'space_anomaly',
      status: 'open',
    })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

watch([page], () => void load())
onMounted(load)
</script>

<template>
  <div>
    <h1 class="ag-page-title">异常队列</h1>
    <p class="ag-page-subtitle">
      异常空间单独告警、只读展示；修复需按治理流程线下处理，后台不提供自动修复。
    </p>

    <div class="ag-card">
      <PageState v-if="state !== 'ready'" :state="state" empty-text="队列为空" @retry="load" />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="当前没有待处理异常" />
        </div>
        <template v-else>
          <div class="ag-cards">
            <div
              v-for="item in data.items"
              :key="`${item.kind}-${item.reference_id}`"
              class="ag-card-row"
              data-testid="anomaly-item"
            >
              <div class="ag-card-row-title">
                {{ item.space_name ?? `空间 #${item.reference_id}` }}
                <span class="ag-tag ag-tag-anomaly">{{ item.status === 'open' ? '待处理' : item.status }}</span>
              </div>
              <div class="ag-card-row-meta">{{ item.anomaly ?? '存在管理异常' }}</div>
              <div class="ag-card-row-meta">记录于 {{ item.created_at }}</div>
              <div>
                <RouterLink
                  v-if="item.space_id"
                  class="ag-tag"
                  :to="{ name: 'space-detail', params: { spaceId: item.space_id } }"
                >
                  查看空间
                </RouterLink>
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
  </div>
</template>
