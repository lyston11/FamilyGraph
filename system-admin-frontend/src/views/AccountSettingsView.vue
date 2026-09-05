<script setup lang="ts">
/**
 * 账号设置：修改密码 / 修改用户名（均需当前密码）。
 * 成功后后端撤销全部会话 → 本地清会话回登录页（PRD FE-F2）。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AdminApiError } from '@/api/client'
import { ADMIN_ERROR_CODES } from '@/types/api'
import { useAdminAuthStore } from '@/stores/auth'
import { validateAdminPasswordStrength } from '@/utils/passwordPolicy'

const router = useRouter()
const auth = useAdminAuthStore()

// ---- 修改密码 ----
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const passwordSubmitting = ref(false)
const passwordError = ref<string | null>(null)

const passwordValidation = computed(() => {
  if (!currentPassword.value) return '请输入当前密码'
  const strength = validateAdminPasswordStrength(newPassword.value)
  if (strength) return strength
  if (newPassword.value !== confirmPassword.value) return '两次输入的新密码不一致'
  return null
})

async function onChangePassword(): Promise<void> {
  if (passwordSubmitting.value || passwordValidation.value) return
  passwordSubmitting.value = true
  passwordError.value = null
  try {
    await auth.changePassword(currentPassword.value, newPassword.value)
    await router.replace({ name: 'login', query: { changed: '1' } })
  } catch (error) {
    if (error instanceof AdminApiError && error.code === ADMIN_ERROR_CODES.PASSWORD_TOO_WEAK) {
      passwordError.value = error.message
    } else if (error instanceof AdminApiError && error.status === 401) {
      passwordError.value = '当前密码不正确'
    } else {
      passwordError.value = '修改失败，请稍后重试'
    }
  } finally {
    passwordSubmitting.value = false
  }
}

// ---- 修改用户名 ----
const usernameCurrentPassword = ref('')
const newUsername = ref('')
const usernameSubmitting = ref(false)
const usernameError = ref<string | null>(null)

const usernameValidation = computed(() => {
  if (!usernameCurrentPassword.value) return '请输入当前密码'
  const value = newUsername.value.trim()
  if (!value) return '请输入新用户名'
  if (value.length > 100) return '用户名不能超过 100 字'
  return null
})

async function onChangeUsername(): Promise<void> {
  usernameError.value = null
  if (usernameSubmitting.value || usernameValidation.value) return
  usernameSubmitting.value = true
  try {
    await auth.changeUsername(usernameCurrentPassword.value, newUsername.value.trim())
    // 后端撤销全部会话：回登录页（用新用户名重新登录）
    await router.replace({ name: 'login', query: { changed: '1' } })
  } catch (error) {
    if (error instanceof AdminApiError && error.code === ADMIN_ERROR_CODES.USERNAME_TAKEN) {
      usernameError.value = error.message
    } else if (error instanceof AdminApiError && error.status === 401) {
      usernameError.value = '当前密码不正确'
    } else {
      usernameError.value = '修改失败，请稍后重试'
    }
  } finally {
    usernameSubmitting.value = false
  }
}
</script>

<template>
  <div>
    <h1 class="ag-page-title">账号设置</h1>
    <p class="ag-page-subtitle">修改成功后需要重新登录（所有会话将被撤销）。</p>

    <section class="ag-card" aria-labelledby="change-password-title">
      <h2 id="change-password-title" class="ag-page-title" style="font-size: 15px">修改密码</h2>
      <form novalidate @submit.prevent="onChangePassword">
        <div class="form-field">
          <label for="settings-current-password">当前密码</label>
          <input
            id="settings-current-password"
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            data-testid="settings-current-password"
          />
        </div>
        <div class="form-field">
          <label for="settings-new-password">新密码（≥12 位，含大小写字母与数字）</label>
          <input
            id="settings-new-password"
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            data-testid="settings-new-password"
          />
        </div>
        <div class="form-field">
          <label for="settings-confirm-password">确认新密码</label>
          <input
            id="settings-confirm-password"
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            data-testid="settings-confirm-password"
          />
        </div>
        <p
          v-if="(passwordError || newPassword) && passwordValidation"
          class="form-error"
          role="alert"
          data-testid="settings-password-validation"
        >
          {{ passwordValidation }}
        </p>
        <p v-if="passwordError" class="form-error" role="alert" data-testid="settings-password-error">
          {{ passwordError }}
        </p>
        <div class="form-actions">
          <button
            type="submit"
            class="ag-tag ag-btn-primary"
            :disabled="passwordSubmitting"
            data-testid="settings-password-submit"
          >
            {{ passwordSubmitting ? '提交中…' : '修改密码' }}
          </button>
        </div>
      </form>
    </section>

    <section class="ag-card" aria-labelledby="change-username-title">
      <h2 id="change-username-title" class="ag-page-title" style="font-size: 15px">修改用户名</h2>
      <form novalidate @submit.prevent="onChangeUsername">
        <div class="form-field">
          <label for="settings-username-current-password">当前密码</label>
          <input
            id="settings-username-current-password"
            v-model="usernameCurrentPassword"
            type="password"
            autocomplete="current-password"
            data-testid="settings-username-current-password"
          />
        </div>
        <div class="form-field">
          <label for="settings-new-username">新用户名</label>
          <input
            id="settings-new-username"
            v-model="newUsername"
            type="text"
            data-testid="settings-new-username"
          />
        </div>
        <p
          v-if="(usernameError || newUsername) && usernameValidation"
          class="form-error"
          role="alert"
          data-testid="settings-username-validation"
        >
          {{ usernameValidation }}
        </p>
        <p v-if="usernameError" class="form-error" role="alert" data-testid="settings-username-error">
          {{ usernameError }}
        </p>
        <div class="form-actions">
          <button
            type="submit"
            class="ag-tag ag-btn-primary"
            :disabled="usernameSubmitting"
            data-testid="settings-username-submit"
          >
            {{ usernameSubmitting ? '提交中…' : '修改用户名' }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>
