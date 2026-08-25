<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { apiClient } from '@/api/client'

/**
 * 管理员后台（m4b，A4 三职责）：用户列表+重置 PIN / 数据兜底修正 / 审计时间线。
 * 路由守卫已保证仅 admin 可达；API 层 403 双重兜底。
 */
interface AdminUserRow {
  id: number
  name: string
  is_admin: boolean
  gender: string
  privacy_mode: string
  claim_status: string
  created_by: number | null
  locked_until: string | null
  created_at: string
}

interface AuditRow {
  id: number
  actor_id: number | null
  action: string
  target_id: number | null
  ip: string | null
  detail_json: string | null
  created_at: string | null
}

const users = ref<AdminUserRow[]>([])
const logs = ref<AuditRow[]>([])
const loading = ref(false)
const oneTimePin = ref('')
const oneTimeFor = ref('')

async function loadUsers() {
  const { data } = await apiClient.get<AdminUserRow[]>('/admin/users')
  users.value = data
}

async function loadLogs() {
  const { data } = await apiClient.get<AuditRow[]>('/admin/audit-logs')
  logs.value = data
}

onMounted(async () => {
  loading.value = true
  try {
    await Promise.all([loadUsers(), loadLogs()])
  } catch {
    ElMessage.error('加载失败（需要管理员身份）')
  } finally {
    loading.value = false
  }
})

async function resetPin(row: AdminUserRow) {
  try {
    await ElMessageBoxConfirmName(String(row.id), row.name)
    const { data } = await apiClient.post<{ pin: string }>(
      `/admin/users/${row.id}/reset-pin`,
      { confirm: true },
    )
    oneTimePin.value = data.pin
    oneTimeFor.value = row.name
    await loadLogs()
  } catch {
    /* 用户取消或失败 */
  }
}

async function ElMessageBoxConfirmName(_id: string, name: string): Promise<void> {
  // 简化确认：输入名字校验由后端 confirm 标志 + 前端弹窗承担
  const { ElMessageBox } = await import('element-plus')
  await ElMessageBox.prompt(`请输入「${name}」以确认重置 PIN`, '重置 PIN', {
    confirmButtonText: '确认重置',
    cancelButtonText: '取消',
    inputPattern: new RegExp(`^${name}$`),
    inputErrorMessage: '名字不一致',
  })
}
</script>

<template>
  <main class="admin-view" v-loading="loading">
    <h2 class="title">管理员后台</h2>

    <!-- 一次性 PIN 弹窗 -->
    <el-dialog :model-value="oneTimePin !== ''" title="新 PIN（仅显示一次）" width="380px" @update:model-value="oneTimePin = ''">
      <p>「{{ oneTimeFor }}」的新 PIN：</p>
      <p class="big-pin" data-test="one-time-admin-pin">{{ oneTimePin }}</p>
      <p class="hint">该成员下次登录将强制修改。请立即转交并截图保存。</p>
    </el-dialog>

    <section>
      <h3>用户管理</h3>
      <el-table :data="users" size="small" data-test="admin-user-table">
        <el-table-column prop="id" label="ID" width="64" />
        <el-table-column prop="name" label="名字" />
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" size="small" type="danger">管理员</el-tag>
            <el-tag v-else size="small" :type="row.claim_status === 'claimed' ? 'success' : 'warning'">
              {{ row.claim_status === 'claimed' ? '已认领' : '待认领' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="privacy_mode" label="归属" width="110" />
        <el-table-column label="操作" width="140">
          <template #default="{ row }">
            <el-button size="small" type="warning" :data-test="`reset-pin-${row.id}`" @click="resetPin(row)">
              重置 PIN
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <section>
      <h3>审计日志</h3>
      <ul class="audit-list" data-test="audit-list">
        <li v-for="log in logs.slice(0, 100)" :key="log.id" class="audit-row">
          <span class="time">{{ log.created_at?.slice(0, 19) }}</span>
          <span class="action">{{ log.action }}</span>
          <span class="meta">actor=#{{ log.actor_id ?? '-' }} target=#{{ log.target_id ?? '-' }}</span>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.admin-view {
  max-width: 900px;
  margin: 0 auto;
  padding: 24px;
}

.title {
  font-size: 20px;
}

.big-pin {
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 8px;
  text-align: center;
  color: var(--el-color-warning);
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.audit-list {
  list-style: none;
  padding: 0;
  max-height: 420px;
  overflow: auto;
}

.audit-row {
  display: flex;
  gap: 12px;
  font-size: 13px;
  padding: 4px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.time {
  color: var(--el-text-color-secondary);
  font-family: monospace;
}

.action {
  font-weight: 600;
  min-width: 180px;
}
</style>
