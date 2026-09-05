<script setup lang="ts">
/**
 * 空间管理员列表（聚合行：同一用户在不同空间分别聚合）。
 * 分页 + 搜索 + 账号状态筛选；点击进入"其所管空间"（管理员→空间主路径）。
 */
import { onMounted, ref, watch } from 'vue'
import { apiSpaceAdmins } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminPageOut, AdminSpaceAdminOut } from '@/types/api'

const data = ref<AdminPageOut<AdminSpaceAdminOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')

const page = ref(1)
const pageSize = 20
const search = ref('')
const searchCommitted = ref('')
const statusFilter = ref<'' | 'managed' | 'claimed'>('')

async function load(): Promise<void> {
  state.value = 'loading'
  try {
    data.value = await apiSpaceAdmins({
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
    <h1 class="ag-page-title">空间管理员</h1>
    <p class="ag-page-subtitle">按管理员聚合；点击查看其所管空间。</p>

    <div class="ag-card">
      <div class="ag-toolbar">
        <form class="ag-inline-form" @submit.prevent="submitSearch">
          <input
            v-model="search"
            type="search"
            placeholder="搜索管理员姓名"
            aria-label="搜索管理员姓名"
            data-testid="space-admins-search"
          />
          <button type="submit" class="ag-tag">搜索</button>
        </form>
        <label class="ag-filter-label">
          账号状态
          <select v-model="statusFilter" aria-label="按账号状态筛选" data-testid="space-admins-status">
            <option value="">全部</option>
            <option value="managed">待认领（managed）</option>
            <option value="claimed">已认领（claimed）</option>
          </select>
        </label>
      </div>

      <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无空间管理员" @retry="load" />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="没有匹配的空间管理员" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="space-admins-table">
              <thead>
                <tr>
                  <th>姓名</th>
                  <th>性别</th>
                  <th>档案状态</th>
                  <th>账号状态</th>
                  <th>所管空间数</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in data.items" :key="item.admin_user_id">
                  <td>{{ item.name }}</td>
                  <td>{{ item.gender }}</td>
                  <td>
                    {{ item.profile_status === 'identity_confirmed' ? '已确认' : '待确认' }}
                  </td>
                  <td>{{ item.account_status === 'claimed' ? '已认领' : '待认领' }}</td>
                  <td>{{ item.space_count }}</td>
                  <td>
                    <RouterLink
                      class="ag-tag"
                      :to="{ name: 'space-admin-spaces', params: { adminUserId: item.admin_user_id } }"
                    >
                      所管空间
                    </RouterLink>
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
