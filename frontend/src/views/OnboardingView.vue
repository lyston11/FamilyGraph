<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'

/**
 * 首启引导页：系统无任何用户时创建管理员。
 * 随机 PIN 仅本次展示（Q3 默认方案），提示截图保存、不可回看。
 */
const auth = useAuthStore()
const router = useRouter()

const name = ref('')
const submitting = ref(false)
const errorMessage = ref('')
// 一次性凭据：仅存在于本组件内存
const issuedPin = ref('')

async function submit(): Promise<void> {
  if (!name.value.trim()) {
    errorMessage.value = '请输入管理员名字'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    issuedPin.value = await auth.initializeAdmin(name.value.trim())
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '初始化失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function goLogin(): void {
  void router.replace({ name: 'login' })
}
</script>

<template>
  <main class="onboarding-view">
    <el-card class="card" data-test="onboarding-card">
      <template v-if="!issuedPin">
        <h1 class="title">欢迎使用 FamilyGraph</h1>
        <p class="desc">首次启动，请创建管理员账号。系统将生成一次性随机 PIN 码。</p>

        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="管理员名字">
            <el-input v-model="name" placeholder="例如：族长" data-test="onboarding-name" />
          </el-form-item>
          <p v-if="errorMessage" class="error" data-test="onboarding-error">{{ errorMessage }}</p>
          <el-button
            type="primary"
            class="submit"
            :loading="submitting"
            data-test="onboarding-submit"
            @click="submit"
          >
            创建管理员
          </el-button>
        </el-form>
      </template>

      <template v-else>
        <h1 class="title">请立即保存 PIN 码</h1>
        <el-alert type="warning" :closable="false" show-icon class="warn">
          该 PIN 码仅显示这一次，关闭后不可回看。请截图或抄写保存。
        </el-alert>
        <div class="pin-display" data-test="one-time-pin">{{ issuedPin }}</div>
        <p class="admin-name">管理员：{{ name }}</p>
        <el-button type="primary" class="submit" data-test="onboarding-done" @click="goLogin">
          我已保存，去登录
        </el-button>
      </template>
    </el-card>
  </main>
</template>

<style scoped>
.onboarding-view {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.card {
  width: 400px;
}

.title {
  margin: 0 0 8px;
  font-size: 22px;
  text-align: center;
}

.desc {
  color: var(--el-text-color-secondary);
}

.warn {
  margin: 12px 0;
}

.pin-display {
  margin: 20px 0;
  padding: 16px;
  text-align: center;
  font-size: 40px;
  letter-spacing: 10px;
  font-family: monospace;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  user-select: all;
}

.admin-name {
  text-align: center;
  color: var(--el-text-color-secondary);
}

.error {
  color: var(--el-color-danger);
  font-size: 13px;
}

.submit {
  width: 100%;
}
</style>
