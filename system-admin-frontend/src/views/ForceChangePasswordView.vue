<script setup lang="ts">
/**
 * 首登强制改密页（password_must_change=true 时路由守卫唯一放行的业务页）。
 * 调用 PUT /admin-api/auth/password（需 current_password）；成功后后端撤销
 * 全部会话，本地清会话回登录页重新登录。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AdminApiError } from '@/api/client'
import { ADMIN_ERROR_CODES } from '@/types/api'
import { useAdminAuthStore } from '@/stores/auth'
import { validateAdminPasswordStrength } from '@/utils/passwordPolicy'

const router = useRouter()
const auth = useAdminAuthStore()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const touched = ref(false)

const strengthMessage = computed(() => validateAdminPasswordStrength(newPassword.value))

const validationMessage = computed(() => {
  if (!currentPassword.value) return '请输入当前密码'
  if (strengthMessage.value) return strengthMessage.value
  if (newPassword.value !== confirmPassword.value) return '两次输入的新密码不一致'
  return null
})

async function onSubmit(): Promise<void> {
  touched.value = true
  if (submitting.value || validationMessage.value) return
  submitting.value = true
  errorMessage.value = null
  try {
    await auth.changePassword(currentPassword.value, newPassword.value)
    // 会话已被后端撤销并本地清理；回登录页重新登录
    await router.replace({ name: 'login', query: { changed: '1' } })
  } catch (error) {
    if (error instanceof AdminApiError && error.code === ADMIN_ERROR_CODES.PASSWORD_TOO_WEAK) {
      errorMessage.value = error.message
    } else if (error instanceof AdminApiError && error.status === 401) {
      errorMessage.value = '当前密码不正确'
    } else {
      errorMessage.value = '修改失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>修改初始密码</h1>
      <p class="auth-sub">首次登录必须修改初始密码后才能使用后台。</p>
      <form novalidate @submit.prevent="onSubmit">
        <div class="form-field">
          <label for="force-current-password">当前密码</label>
          <input
            id="force-current-password"
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            required
            data-testid="force-current-password"
          />
        </div>
        <div class="form-field">
          <label for="force-new-password">新密码（≥12 位，含大小写字母与数字）</label>
          <input
            id="force-new-password"
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            required
            data-testid="force-new-password"
          />
        </div>
        <div class="form-field">
          <label for="force-confirm-password">确认新密码</label>
          <input
            id="force-confirm-password"
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            required
            data-testid="force-confirm-password"
          />
        </div>
        <p
          v-if="(touched || newPassword) && validationMessage"
          class="form-error"
          role="alert"
          data-testid="force-validation"
        >
          {{ validationMessage }}
        </p>
        <p v-if="errorMessage" class="form-error" role="alert" data-testid="force-error">
          {{ errorMessage }}
        </p>
        <button
          type="submit"
          class="ag-tag ag-btn-primary ag-btn-block"
          :disabled="submitting"
          data-testid="force-submit"
        >
          {{ submitting ? '提交中…' : '确认修改并重新登录' }}
        </button>
      </form>
    </div>
  </div>
</template>
