<script setup lang="ts">
/**
 * 读取审计：admin_access_audits 时间线（永久保留；理由正文不落审计）。
 * 支持目标类型/目标 ID 筛选 + 分页。只读，无任何写操作。
 */
import { onMounted, ref, watch } from 'vue'
import { apiAccessAudits } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminAuditAccessOut, AdminPageOut } from '@/types/api'

const data = ref<AdminPageOut<AdminAuditAccessOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20
const targetTypeFilter = ref<'' | 'user' | 'space'>('')
const targetIdInput = ref('')

async function load(): Promise<void> {
  state.value = 'loading'
  const targetId = targetIdInput.value.trim()
  const parsedId = Number(targetId)
  const validTargetId = targetId !== '' && Number.isInteger(parsedId) && parsedId > 0
  try {
    data.value = await apiAccessAudits({
      page: page.value,
      pageSize,
      targetType: targetTypeFilter.value || null,
      targetId: validTargetId ? parsedId : null,
    })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

function applyFilters(): void {
  page.value = 1
  void load()
}

watch([page], () => void load())
onMounted(load)
</script>

<template>
  <div>
    <h1 class="ag-page-title">读取审计</h1>
    <p class="ag-page-subtitle">管理读取与访问会话使用记录；审计永久保留，仅本页面可查。</p>

    <div class="ag-card">
      <div class="ag-toolbar">
        <label class="ag-filter-label">
          目标类型
          <select
            v-model="targetTypeFilter"
            aria-label="按目标类型筛选"
            data-testid="audit-target-type"
            @change="applyFilters"
          >
            <option value="">全部</option>
            <option value="user">成员档案</option>
            <option value="space">空间</option>
          </select>
        </label>
        <label class="ag-filter-label">
          目标 ID
          <input
            v-model="targetIdInput"
            type="number"
            min="1"
            aria-label="目标 ID"
            data-testid="audit-target-id"
          />
        </label>
        <button type="button" class="ag-tag" @click="applyFilters">应用筛选</button>
      </div>

      <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无审计记录" @retry="load" />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="没有匹配的审计记录" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="audit-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>动作</th>
                  <th>目标</th>
                  <th>端点</th>
                  <th>结果数</th>
                  <th>管理员</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="entry in data.items" :key="entry.id">
                  <td>{{ entry.created_at }}</td>
                  <td>{{ entry.action }}</td>
                  <td>
                    <template v-if="entry.target_type">
                      {{ entry.target_type === 'user' ? '成员' : '空间' }} #{{ entry.target_id }}
                    </template>
                    <span v-else>-</span>
                  </td>
                  <td>{{ entry.endpoint }}</td>
                  <td>{{ entry.result_count ?? '-' }}</td>
                  <td>{{ entry.system_admin_id === null ? '-' : `#${entry.system_admin_id}` }}</td>
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
