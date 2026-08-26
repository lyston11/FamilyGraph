<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import ChangePinForm from '@/components/common/ChangePinForm.vue'
import DataRightsPanel from '@/components/member/DataRightsPanel.vue'
import DisclosureMatrix from '@/components/member/DisclosureMatrix.vue'
import { useAuthStore } from '@/stores/auth'

/**
 * 设置页（v2）：改名 / 改 PIN / 登出 + 披露偏好矩阵（§0.1）+
 * 我的数据（F-5：导出/更正/删除/争议）。
 */
const auth = useAuthStore()
const router = useRouter()

const nameForm = reactive({ name: auth.user?.name ?? '' })
const savingName = ref(false)
const nameError = ref('')

async function saveName(): Promise<void> {
  if (!nameForm.name.trim()) {
    nameError.value = '名字不能为空'
    return
  }
  savingName.value = true
  nameError.value = ''
  try {
    await auth.updateName(nameForm.name.trim())
    ElMessage.success('名字已更新')
  } catch (error) {
    nameError.value = error instanceof ApiError ? error.message : '保存失败，请稍后重试'
  } finally {
    savingName.value = false
  }
}

async function doLogout(): Promise<void> {
  await auth.logout()
  ElMessage.success('已退出登录')
  void router.replace({ name: 'login' })
}
</script>

<template>
  <main class="settings-view">
    <el-card class="card" data-test="settings-card">
      <template #header>
        <div class="header">
          <span>设置</span>
          <el-button text type="primary" data-test="go-memory" @click="router.push('/memory')">记忆与知识</el-button>
      <el-button text type="danger" data-test="logout-btn" @click="doLogout">退出登录</el-button>
        </div>
      </template>

      <section class="section">
        <h2 class="section-title">当前账号</h2>
        <p class="meta" data-test="current-user">
          {{ auth.user?.name }}<template v-if="auth.user?.is_admin">（平台运营者）</template>
        </p>
      </section>

      <section class="section">
        <h2 class="section-title">修改名字</h2>
        <el-form inline @submit.prevent="saveName">
          <el-form-item label="新名字">
            <el-input v-model="nameForm.name" data-test="name-input" aria-label="新名字" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="savingName" data-test="name-save" @click="saveName">
              保存
            </el-button>
          </el-form-item>
        </el-form>
        <p v-if="nameError" class="error" data-test="name-error">{{ nameError }}</p>
      </section>

      <section class="section">
        <h2 class="section-title">修改 PIN 码</h2>
        <ChangePinForm />
      </section>

      <section class="section" data-test="memory-entry-section">
        <h2 class="section-title">长期知识</h2>
        <p class="meta">管理待确认记忆、共享范围和可追溯的知识引用。</p>
        <el-button type="primary" plain data-test="go-memory" @click="router.push('/memory')">
          打开记忆与知识
        </el-button>
      </section>

      <section class="section" data-test="disclosure-section">
        <h2 class="section-title">披露偏好</h2>
        <DisclosureMatrix />
      </section>

      <section class="section" data-test="data-rights-section">
        <h2 class="section-title">我的数据</h2>
        <DataRightsPanel />
      </section>
    </el-card>
  </main>
</template>

<style scoped>
.settings-view {
  display: flex;
  justify-content: center;
  min-height: 100vh;
  padding: 40px 16px;
}

.card {
  width: 640px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section {
  margin-bottom: 28px;
}

.section-title {
  margin: 0 0 12px;
  font-size: 15px;
  color: var(--el-text-color-primary);
}

.meta {
  margin: 0;
  color: var(--el-text-color-secondary);
}

.error {
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
