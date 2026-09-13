<script setup lang="ts">
/**
 * 某空间管理员所管空间列表（管理员→空间钻取第二跳）。
 * 未知管理员后端返回 200 空页 → 显示安全空态，不暴露存在性。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiSpaceAdminSpaces } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type { AdminPageOut, AdminSpaceSummaryOut } from '@/types/api'

const route = useRoute()
const adminUserId = computed(() => Number(route.params['adminUserId']))

const data = ref<AdminPageOut<AdminSpaceSummaryOut> | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const page = ref(1)
const pageSize = 20

async function load(): Promise<void> {
  if (!Number.isInteger(adminUserId.value) || adminUserId.value <= 0) {
    state.value = 'error'
    return
  }
  state.value = 'loading'
  try {
    data.value = await apiSpaceAdminSpaces(adminUserId.value, { page: page.value, pageSize })
    state.value = 'ready'
  } catch {
    state.value = 'error'
  }
}

watch([page, adminUserId], () => void load())
onMounted(load)
</script>

<template>
  <div>
    <nav class="ag-breadcrumb" aria-label="面包屑">
      <RouterLink class="ag-back-link" :to="{ name: 'space-admins' }" data-testid="back-to-space-admins">
        <span aria-hidden="true">←</span>
        返回空间管理员
      </RouterLink>
      <span>/</span>
      <span>所管空间</span>
    </nav>
    <h1 class="ag-page-title">所管空间</h1>
    <p class="ag-page-subtitle">管理员 #{{ adminUserId }} 名下的空间；点击进入空间详情。</p>

    <div class="ag-card">
      <PageState v-if="state !== 'ready'" :state="state" empty-text="该管理员暂无在管空间" @retry="load" />
      <template v-else-if="data">
        <div v-if="data.items.length === 0">
          <PageState state="empty" empty-text="该管理员暂无在管空间" />
        </div>
        <template v-else>
          <div class="ag-table-wrap">
            <table class="ag-table" data-testid="admin-spaces-table">
              <thead>
                <tr>
                  <th>空间</th>
                  <th>类型</th>
                  <th>在任管理员</th>
                  <th>创建时间</th>
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
                  <td>
                    <template v-if="item.manager_name">{{ item.manager_name }}</template>
                    <span v-else class="ag-tag ag-tag-warning">无管理员</span>
                  </td>
                  <td>{{ item.created_at }}</td>
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
