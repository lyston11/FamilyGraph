<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput } from 'naive-ui'

import * as authApi from '@/api/auth'
import { ApiError } from '@/api/errors'
import { getSafeSystemAdminRedirect } from '@/router/redirect'
import { useAuthStore } from '@/stores/auth'

/**
 * 系统管理员真实登录页（SAR-F1，09-01-system-admin-governance-routes）：
 * - 复用唯一登录端点 `/api/auth/login`（共享 API decoder），不新增第二套凭据；
 * - 响应必须 `principal_type === 'system_admin'` 才落会话：family_user 凭据
 *   不能建立系统管理员会话，未知/缺失主体整体拒绝并清理任何临时 auth 状态；
 * - 错误文案统一且不泄露账号是否存在（凭据失败沿用后端统一文案）；
 * - 回跳只接受站内已知 system-admin 路由（getSafeSystemAdminRedirect），
 *   登录成功按 `pin_must_change` 进入 PIN 首改或后台；
 * - 已登录主体访问本页由路由守卫互斥分流（system_admin → 后台/首改，
 *   family_user → 家庭壳），本页不重复处理。
 */
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

/** family_user / 未知主体的统一拒绝文案：引导家庭入口，不进入后台 */
const PRINCIPAL_REJECTION_MESSAGE = '该入口仅限系统管理员登录；家庭用户请使用家庭登录入口'

const form = reactive({ name: '', pin: '' })
const submitting = ref(false)
const succeeded = ref(false)
const errorMessage = ref('')

function redirectAfterLogin(): void {
  // 只允许站内已知 system-admin 路由；其余一律回后台首页
  const target = getSafeSystemAdminRedirect(route.query.redirect)
  if (auth.mustChangePin) {
    void router.replace({
      name: 'force-change-pin',
      query: target ? { redirect: target } : undefined,
    })
    return
  }
  void router.replace(target ?? { name: 'system-admin' })
}

async function submit(): Promise<void> {
  // 提交中 / 已成功：按钮禁用之外再硬挡一次，防止重复提交
  if (submitting.value || succeeded.value) return
  if (!form.name.trim() || !/^\d{6}$/.test(form.pin)) {
    errorMessage.value = '请输入登录名和 6 位数字 PIN 码'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const pair = await authApi.login(form.name.trim(), form.pin)
    // 硬校验主体（先于任何会话写入）：家庭主体/未知主体一律拒绝
    if (pair.user?.principal_type !== 'system_admin') {
      auth.clearSession()
      errorMessage.value = PRINCIPAL_REJECTION_MESSAGE
      return
    }
    auth.applySystemAdminSession(pair)
    succeeded.value = true
    redirectAfterLogin()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      // 同名同 PIN 消歧只服务家庭用户（system_admin 命中直接签发）：
      // 出现 409 即家庭凭据，按统一拒绝处理，不在后台页消歧家庭会话
      errorMessage.value = PRINCIPAL_REJECTION_MESSAGE
    } else {
      errorMessage.value = error instanceof ApiError ? error.message : '登录失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="system-admin-login" data-test="system-admin-login-view">
    <section class="plate" aria-labelledby="system-admin-login-title">
      <p class="eyebrow">SYSTEM ADMIN</p>
      <h1 id="system-admin-login-title">系统管理员入口</h1>
      <p class="hint">系统管理员使用独立主体登录，与家庭用户会话互斥。</p>

      <NForm class="form" label-placement="top" :show-feedback="false" @submit.prevent="submit">
        <NFormItem label="登录名" :label-props="{ for: 'system-admin-login-name-input' }">
          <NInput
            v-model:value="form.name"
            placeholder="请输入登录名"
            :input-props="{ id: 'system-admin-login-name-input' }"
            data-test="system-admin-login-name"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem label="PIN 码" :label-props="{ for: 'system-admin-login-pin-input' }">
          <NInput
            v-model:value="form.pin"
            type="password"
            show-password-on="click"
            :maxlength="6"
            placeholder="6 位数字 PIN 码"
            :input-props="{ id: 'system-admin-login-pin-input', inputmode: 'numeric' }"
            data-test="system-admin-login-pin"
            @keyup.enter="submit"
          />
        </NFormItem>

        <p v-if="errorMessage" class="error" role="alert" data-test="system-admin-login-error">
          {{ errorMessage }}
        </p>

        <NButton
          class="submit"
          type="primary"
          block
          :loading="submitting"
          :disabled="succeeded"
          data-test="system-admin-login-submit"
          @click="submit"
        >
          登录
        </NButton>
      </NForm>
    </section>
  </main>
</template>

<style scoped>
.system-admin-login {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

.plate {
  width: min(420px, 100%);
  padding: 36px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
}

.eyebrow {
  margin: 0 0 6px;
  color: var(--fg-accent);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0 0 12px;
  font-family: var(--fg-font-display);
  font-size: 24px;
  color: var(--fg-ink);
}

.hint {
  margin: 0 0 20px;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.error {
  margin: 0;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--fg-status-disputed);
  background-color: color-mix(in srgb, var(--fg-status-disputed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-disputed) 35%, transparent);
  border-radius: var(--fg-radius-control);
}

.submit {
  margin-top: 8px;
}
</style>
