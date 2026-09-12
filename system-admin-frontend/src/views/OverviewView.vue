<script setup lang="ts">
/**
 * 概览：全局统计 + 空间健康列表（分页/搜索/状态筛选）+ 异常队列入口。
 * 无管理员/异常空间单独标注；不提供任何修复动作（只读）。
 */
import { computed, onMounted, ref, watch } from 'vue'
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
const viewFilter = ref<'family' | 'all' | 'household' | 'lineage'>('family')

type FamilyOverviewRow = {
  key: string
  primary: AdminOverviewPageOut['items'][number]
  household: AdminOverviewPageOut['items'][number] | null
  lineage: AdminOverviewPageOut['items'][number] | null
  hasLineageLink: boolean
  status: 'healthy' | 'anomaly'
  anomalies: string[]
}

const familyRows = computed((): FamilyOverviewRow[] => {
  if (!data.value) return []
  const items = data.value.items
  const linkedHouseholds = new Set<number>()
  const rows: FamilyOverviewRow[] = []
  const hasExplicitLinks = items.some((item) => item.lineage_space_id != null)
  const managerHouseholds = new Map<number, typeof items>()
  if (!hasExplicitLinks) {
    for (const item of items) {
      const target = managerHouseholds
      if (item.manager_user_id === null) continue
      target.set(item.manager_user_id, [...(target.get(item.manager_user_id) ?? []), item])
    }
  }

  for (const lineage of items.filter((item) => item.kind === 'lineage')) {
    const explicitHousehold = items.find(
      (item) => item.kind === 'household' && item.lineage_space_id === lineage.space_id,
    )
    const fallbackHouseholds = lineage.manager_user_id === null
      ? []
      : managerHouseholds.get(lineage.manager_user_id) ?? []
    const household = explicitHousehold
      ?? (fallbackHouseholds.length === 1 ? fallbackHouseholds[0] : null)
    if (household) linkedHouseholds.add(household.space_id)
    const parts = [lineage, household].filter((item): item is NonNullable<typeof item> => item !== null)
    rows.push({
      key: `family-${lineage.space_id}`,
      primary: lineage,
      household,
      lineage,
      hasLineageLink: true,
      status: parts.some((item) => item.status === 'anomaly') ? 'anomaly' : 'healthy',
      anomalies: [...new Set(parts.flatMap((item) => item.anomalies))],
    })
  }

  for (const household of items.filter((item) => item.kind === 'household')) {
    if (linkedHouseholds.has(household.space_id)) continue
    rows.push({
      key: `household-${household.space_id}`,
      primary: household,
      household,
      lineage: null,
      hasLineageLink: household.lineage_space_id != null,
      status: household.status,
      anomalies: household.anomalies,
    })
  }

  // 保留后端分页中可能出现的孤立族谱空间，并稳定按主空间 id 排序。
  return rows
    .filter((row) => {
      if (viewFilter.value === 'all' || viewFilter.value === 'family') return true
      if (viewFilter.value === 'household') return row.household !== null
      return row.lineage !== null
    })
    .sort((a, b) => a.primary.space_id - b.primary.space_id)
})

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
  <div class="admin-overview">
    <div class="ag-page-intro">
      <div>
        <div class="ag-overline">SYSTEM PULSE / 01</div>
        <h1 class="ag-page-title">概览</h1>
        <p class="ag-page-subtitle">空间健康与管理员归属总览；异常空间只读展示，不做自动修复。</p>
      </div>
      <div class="ag-intro-note"><span class="admin-context-dot"></span> 数据来自当前治理快照</div>
    </div>

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
            placeholder="搜索家族或家庭名称"
            aria-label="搜索家族或家庭名称"
            data-testid="overview-search"
          />
          <button type="submit" class="ag-tag">搜索</button>
        </form>
        <label class="ag-filter-label">
          视图
          <select v-model="viewFilter" aria-label="按空间关系筛选" data-testid="overview-kind">
            <option value="family">家族单元</option>
            <option value="all">全部空间</option>
            <option value="household">家庭授权</option>
            <option value="lineage">族谱视图</option>
          </select>
        </label>
        <label class="ag-filter-label">
          健康
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
        <div v-if="familyRows.length === 0">
          <PageState state="empty" empty-text="没有匹配的空间" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="overview-table">
              <thead>
                <tr>
                  <th>家族单元</th>
                  <th>空间构成</th>
                  <th>成员规模</th>
                  <th>管理员</th>
                  <th>健康状态</th>
                  <th>异常</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in familyRows" :key="row.key">
                  <td>
                    <RouterLink :to="{ name: 'space-detail', params: { spaceId: row.primary.space_id } }">
                      {{ row.lineage?.name ?? row.household?.name ?? row.primary.name }}
                    </RouterLink>
                  </td>
                  <td>
                    <span v-if="row.lineage" class="ag-space-pair">
                      <span class="ag-tag ag-tag-healthy">族谱视图</span>
                      <span v-if="row.household" class="ag-tag ag-tag-neutral">家庭授权</span>
                    </span>
                    <span v-else class="ag-tag ag-tag-neutral">
                      {{ row.hasLineageLink ? '家庭授权（已配对）' : '独立家庭' }}
                    </span>
                  </td>
                  <td>
                    <span v-if="row.household">家庭 {{ row.household.member_count }} · 家族 {{ row.lineage?.member_count ?? '-' }}</span>
                    <span v-else>{{ row.primary.member_count }}</span>
                  </td>
                  <td>
                    <template v-if="row.lineage?.manager_name ?? row.household?.manager_name">
                      {{ row.lineage?.manager_name ?? row.household?.manager_name }}
                    </template>
                    <span v-else class="ag-tag ag-tag-warning">无管理员</span>
                  </td>
                  <td>
                    <span
                      class="ag-tag"
                      :class="row.status === 'anomaly' ? 'ag-tag-anomaly' : 'ag-tag-healthy'"
                    >
                      {{ row.status === 'anomaly' ? '异常' : '健康' }}
                    </span>
                  </td>
                  <td>
                    <ul v-if="row.anomalies.length > 0" class="ag-anomaly-list">
                      <li v-for="anomaly in row.anomalies" :key="anomaly">{{ anomaly }}</li>
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
