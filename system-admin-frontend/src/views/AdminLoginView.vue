<script setup lang="ts">
/**
 * 后台登录页：用户名 + 强密码（/admin-api/auth/login）。
 * - api 层硬校验响应 admin 主体字段，缺失/非 system 会话一律拒绝写会话；
 * - 统一错误文案（防枚举），不区分账号不存在/密码错误/锁定；
 * - redirect 只接受内部路径白名单（resolveSafeRedirect）。
 */
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AdminApiError } from '@/api/client'
import { resolveSafeRedirect } from '@/router/safeRedirect'
import { useAdminAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAdminAuthStore()

const username = ref('')
const password = ref('')
const submitting = ref(false)
const errorMessage = ref<string | null>(null)

const canSubmit = computed(() => username.value.trim() !== '' && password.value !== '')

async function onSubmit(): Promise<void> {
  if (submitting.value || !canSubmit.value) return
  submitting.value = true
  errorMessage.value = null
  try {
    const mustChange = await auth.login(username.value.trim(), password.value)
    const redirect = resolveSafeRedirect(route.query['redirect'])
    if (mustChange) {
      await router.replace({ name: 'force-change-password' })
      return
    }
    await router.replace(redirect ?? { name: 'overview' })
  } catch (error) {
    errorMessage.value =
      error instanceof AdminApiError && error.status > 0
        ? error.message
        : '登录失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-card">
      <h1>FamilyGraph 系统管理后台</h1>
      <p class="auth-sub">系统管理员专用入口，与家庭用户账号相互独立。</p>
      <form novalidate @submit.prevent="onSubmit">
        <div class="form-field">
          <label for="login-username">用户名</label>
          <input
            id="login-username"
            v-model="username"
            type="text"
            autocomplete="username"
            required
            data-testid="login-username"
          />
        </div>
        <div class="form-field">
          <label for="login-password">密码</label>
          <input
            id="login-password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            required
            data-testid="login-password"
          />
        </div>
        <p v-if="errorMessage" class="form-error" role="alert" data-testid="login-error">
          {{ errorMessage }}
        </p>
        <button
          type="submit"
          class="ag-tag ag-btn-primary ag-btn-block"
          :disabled="submitting || !canSubmit"
          data-testid="login-submit"
        >
          {{ submitting ? '登录中…' : '登录' }}
        </button>
      </form>
      <p v-if="route.query['expired'] === '1'" class="form-error ag-mt-12">
        会话已过期，请重新登录。
      </p>
    </div>
  </div>
</template>
