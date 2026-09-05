<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { getSafeInternalRedirect } from '@/router/redirect'
import { useAuthStore } from '@/stores/auth'

/**
 * 自助注册页（沉浸页，meta.chrome='blank'；09-05 决策 3/19）。
 *
 * - 表单只收「用户名 + 自选 PIN + 可选邀请码」：后端已将显示名并入用户名
 *   （users.name 单字段），不设单独「显示名」输入（任务约定）。
 * - 支持 ?code=XXX 自动回填（分享链接 /register?code=XXX 的落地端，决策 12）。
 * - 成功即登录态：token 落地沿用 auth store 的 applyTokenPair（与 login 同路径），
 *   跳转沿用登录后逻辑（安全回跳 > home）；新用户 provisional 确档由路由守卫引导。
 * - 错误文案与后端一致（error-handling.md）：用户名占用防枚举统一文案、
 *   码校验失败的码字段级文案，均原样呈现，不做二次改写。
 * - 开关关闭（registration_enabled=false）时本路由不可达：守卫已将其呈现为
 *   与未注册深链同形的普通 404（router/index.ts），本视图无需自行兜底。
 */
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const message = useMessage()

const form = reactive({ name: '', pin: '', pinConfirm: '', code: '' })
const submitting = ref(false)
const errorMessage = ref('')
const codeError = ref('')

/** 无混淆字符集（backend services/invite_codes.CODE_ALPHABET：去 0/O/1/I/L），大小写不敏感 */
const CODE_PATTERN = /^[2-9A-HJKMNP-Z]{8,10}$/

/** 分享链接 ?code= 自动回填：归一大写 + 去空白（与后端 normalize_raw_code 同口径） */
onMounted(() => {
  const raw = route.query.code
  if (typeof raw === 'string' && raw.trim()) {
    form.code = raw.trim().toUpperCase()
  }
})

const codeFilled = computed(() => form.code.trim().length > 0)

function validate(): boolean {
  errorMessage.value = ''
  codeError.value = ''
  if (!form.name.trim()) {
    errorMessage.value = '请输入用户名'
    return false
  }
  if (!/^\d{6}$/.test(form.pin)) {
    errorMessage.value = '请设置 6 位数字 PIN 码'
    return false
  }
  if (form.pinConfirm !== form.pin) {
    errorMessage.value = '两次输入的 PIN 码不一致'
    return false
  }
  if (codeFilled.value && !CODE_PATTERN.test(form.code.trim().toUpperCase())) {
    codeError.value = '邀请码为 8-10 位，不含 0、1、I、L、O'
    return false
  }
  return true
}

function redirectAfterRegister(): void {
  const target = getSafeInternalRedirect(route.query.redirect)
  if (auth.mustChangePin) {
    // 自设 PIN 注册路径 pin_must_change 恒为 false；保留该分支与登录同构，防后端语义变化
    void router.replace({
      name: 'force-change-pin',
      query: target ? { redirect: target } : undefined,
    })
    return
  }
  if (target) {
    void router.replace(target)
    return
  }
  void router.replace({ name: 'home' })
}

async function submit(): Promise<void> {
  if (!validate()) return
  submitting.value = true
  try {
    await auth.register(
      form.name.trim(),
      form.pin,
      codeFilled.value ? form.code.trim().toUpperCase() : undefined,
    )
    message.success('注册成功')
    redirectAfterRegister()
  } catch (error) {
    if (error instanceof ApiError && error.code === 'INVITE_CODE_INVALID') {
      // 码无效/过期/撤销/用尽：后端字段级文案原样呈现（不暴露创建者等信息）
      codeError.value = error.message
    } else {
      errorMessage.value = error instanceof ApiError ? error.message : '注册失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="register-view">
    <section class="plate" aria-labelledby="register-title">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>
      <h1 id="register-title" class="title">注册新账号</h1>
      <p class="subtitle">用户名即你在家庭档案中的名字</p>

      <NForm class="form" label-placement="top" :show-feedback="false" @submit.prevent="submit">
        <NFormItem label="用户名" :label-props="{ for: 'register-name-input' }">
          <NInput
            v-model:value="form.name"
            placeholder="请输入用户名"
            :input-props="{ id: 'register-name-input' }"
            data-test="register-name"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem label="设置 PIN 码" :label-props="{ for: 'register-pin-input' }">
          <NInput
            v-model:value="form.pin"
            type="password"
            show-password-on="click"
            :maxlength="6"
            placeholder="6 位数字 PIN 码"
            :input-props="{ id: 'register-pin-input', inputmode: 'numeric' }"
            data-test="register-pin"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem label="确认 PIN 码" :label-props="{ for: 'register-pin-confirm-input' }">
          <NInput
            v-model:value="form.pinConfirm"
            type="password"
            show-password-on="click"
            :maxlength="6"
            placeholder="再次输入 PIN 码"
            :input-props="{ id: 'register-pin-confirm-input', inputmode: 'numeric' }"
            data-test="register-pin-confirm"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem label="邀请码（可选）" :label-props="{ for: 'register-code-input' }">
          <NInput
            v-model:value="form.code"
            :maxlength="10"
            placeholder="有邀请码请输入（8-10 位）"
            :input-props="{ id: 'register-code-input' }"
            data-test="register-code"
            @keyup.enter="submit"
          />
        </NFormItem>

        <p v-if="codeError" class="error" role="alert" data-test="register-code-error">
          {{ codeError }}
        </p>
        <p v-if="errorMessage" class="error" role="alert" data-test="register-error">
          {{ errorMessage }}
        </p>

        <NButton
          class="submit"
          type="primary"
          block
          :loading="submitting"
          data-test="register-submit"
          @click="submit"
        >
          注册
        </NButton>
      </NForm>

      <p class="to-login">
        已有账号？
        <NButton text type="primary" data-test="register-to-login" @click="router.replace({ name: 'login' })">
          去登录
        </NButton>
      </p>
    </section>
  </main>
</template>

<style scoped>
/* 与登录页同族的沉浸卡基座（tokens 驱动双主题观感，见 LoginView.vue） */
.register-view {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
  background-color: var(--fg-surface);
  background-image:
    radial-gradient(ellipse 80% 60% at 50% 20%, color-mix(in srgb, var(--fg-accent) 12%, transparent), transparent 70%),
    radial-gradient(circle 1.8px at 28px 36px, var(--fg-dot) 100%, transparent),
    radial-gradient(circle 1.2px at 160px 140px, color-mix(in srgb, var(--fg-dot) 75%, transparent) 100%, transparent);
  background-size: 100% 100%, 260px 260px, 190px 190px;
}

.plate {
  position: relative;
  width: min(420px, 100%);
  padding: 40px 36px 32px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.8);
  box-shadow:
    0 16px 48px color-mix(in srgb, var(--fg-ink) 12%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 15%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 30%, transparent);
  box-sizing: border-box;
  animation: plateSlideIn 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes plateSlideIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.96);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 纸墨：证书式内嵌发丝线；清雅不渲染双框 */
.plate::before {
  content: '';
  position: absolute;
  inset: 6px;
  border: 1px solid var(--fg-line);
  border-radius: calc(var(--fg-radius-card) - 2px);
  pointer-events: none;
}

[data-theme='modern'] .plate::before {
  display: none;
}

.brand {
  display: flex;
  justify-content: center;
}

.seal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--fg-radius-control);
  background-color: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-family: var(--fg-font-display);
  font-size: 22px;
  font-weight: 700;
  box-shadow: var(--fg-shadow-card);
}

.title {
  margin: 12px 0 4px;
  text-align: center;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fg-ink);
}

.subtitle {
  margin: 0 0 20px;
  text-align: center;
  font-size: 14px;
  color: var(--fg-ink-secondary);
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
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

.to-login {
  margin: 16px 0 0;
  text-align: center;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}
</style>
