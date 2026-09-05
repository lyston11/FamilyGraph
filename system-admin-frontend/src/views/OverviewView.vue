<script setup lang="ts">
/**
 * 概览：全局统计 + 空间健康列表（分页/搜索/状态筛选）+ 异常队列入口。
 * 无管理员/异常空间单独标注；不提供任何修复动作（只读）。
 */
import { onMounted, ref, watch } from 'vue'
import { apiOverview } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminOverviewPageOut } from '@/types/api'

const data = ref<AdminOverviewPageOut | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')

const page = ref(1)
const pageSize = 20
const search = ref('')
const statusFilter = ref<'' | 'healthy' | 'anomaly'>('')
const searchCommitted = ref('')

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiOverview({
      page: page.value,
      pageSize,
      search: searchCommitted.value || null,
      status: statusFilter.value || null,
    })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

function submitSearch(): void {
  searchCommitted.value = search.value.trim()
  page.value = 1
}

// 搜索/翻页/筛选统一经 watch 触发重载；搜索提交时 page 重置为 1 同 tick 批合并
watch([page, statusFilter, searchCommitted], () => void load())
onMounted(load)
</script>

<template>
  <div>
    <h1 class="ag-page-title">概览</h1>
    <p class="ag-page-subtitle">空间健康与管理员归属总览；异常空间只读展示，不做自动修复。</p>

    <section v-if="data" class="ag-metric-grid" data-testid="overview-totals">
      <div class="ag-metric">
        <div class="ag-metric-value" data-testid="total-spaces">{{ data.totals.spaces_total }}</div>
        <div class="ag-metric-label">空间总数</div>
      </div>
      <div class="ag-metric">
        <div class="ag-metric-value">{{ data.totals.healthy_spaces }}</div>
        <div class="ag-metric-label">健康空间</div>
      </div>
      <div class="ag-metric">
        <div class="ag-metric-value ag-metric-danger" data-testid="anomaly-metric">
          {{ data.totals.anomaly_spaces }}
        </div>
        <div class="ag-metric-label">异常空间</div>
      </div>
      <div class="ag-metric">
        <div class="ag-metric-value">{{ data.totals.active_space_admins }}</div>
        <div class="ag-metric-label">在任空间管理员</div>
      </div>
      <div class="ag-metric">
        <div class="ag-metric-value">{{ data.totals.pending_applications }}</div>
        <div class="ag-metric-label">待裁决申请</div>
      </div>
    </section>

    <div class="ag-card">
      <div class="ag-toolbar">
        <form class="ag-inline-form" @submit.prevent="submitSearch">
          <input
            v-model="search"
            type="search"
            placeholder="搜索空间名称"
            aria-label="搜索空间名称"
            data-testid="overview-search"
          />
          <button type="submit" class="ag-tag">搜索</button>
        </form>
        <label class="ag-filter-label">
          状态
          <select v-model="statusFilter" aria-label="按状态筛选" data-testid="overview-status">
            <option value="">全部</option>
            <option value="healthy">健康</option>
            <option value="anomaly">异常</option>
          </select>
        </label>
        <RouterLink class="ag-tag" :to="{ name: 'anomaly-queue' }" data-testid="anomaly-entry">
          异常队列
        </RouterLink>
      </div>

      <PageState
        v-if="state !== 'ready'"
        :state="state"
        empty-text="暂无空间"
        @retry="load"
      />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="没有匹配的空间" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="overview-table">
              <thead>
                <tr>
                  <th>空间</th>
                  <th>类型</th>
                  <th>成员数</th>
                  <th>管理员</th>
                  <th>健康状态</th>
                  <th>异常</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in data.items" :key="item.space_id">
                  <td>
                    <RouterLink :to="{ name: 'space-detail', params: { spaceId: item.space_id } }">
                      {{ item.name }}
                    </RouterLink>
                  </td>
                  <td>{{ item.kind === 'household' ? '家庭空间' : '族谱空间' }}</td>
                  <td>{{ item.member_count }}</td>
                  <td>
                    <template v-if="item.manager_name">{{ item.manager_name }}</template>
                    <span v-else class="ag-tag ag-tag-warning">无管理员</span>
                  </td>
                  <td>
                    <span
                      class="ag-tag"
                      :class="item.status === 'anomaly' ? 'ag-tag-anomaly' : 'ag-tag-healthy'"
                    >
                      {{ item.status === 'anomaly' ? '异常' : '健康' }}
                    </span>
                  </td>
                  <td>
                    <ul v-if="item.anomalies.length > 0" class="ag-anomaly-list">
                      <li v-for="anomaly in item.anomalies" :key="anomaly">{{ anomaly }}</li>
                    </ul>
                    <span v-else>-</span>
                  </td>
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
  </div>
</template>
