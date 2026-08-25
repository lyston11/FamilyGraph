<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import ChangePinForm from '@/components/common/ChangePinForm.vue'
import { useAuthStore } from '@/stores/auth'

/**
 * 设置页：改名 / 改 PIN / 登出（PRD m0b 前端范围）。
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
          <el-button text type="danger" data-test="logout-btn" @click="doLogout">退出登录</el-button>
        </div>
      </template>

      <section class="section">
        <h2 class="section-title">当前账号</h2>
        <p class="meta" data-test="current-user">
          {{ auth.user?.name }}<template v-if="auth.user?.is_admin">（管理员）</template>
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
  width: 480px;
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
