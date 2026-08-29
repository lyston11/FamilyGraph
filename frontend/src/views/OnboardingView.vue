<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'

/**
 * 首启引导页（沉浸页，meta.chrome='blank'）：系统无任何用户时创建平台运营者。
 * 随机 PIN 仅本次展示（Q3 默认方案），提示截图保存、不可回看——以"凭证卡"呈现：
 * 大号等宽数字 + 复制按钮 + 票据式警示框；纸墨主题附朱砂"凭"字印章质感。
 */
const auth = useAuthStore()
const router = useRouter()

const name = ref('')
const submitting = ref(false)
const errorMessage = ref('')
// 一次性凭据：仅存在于本组件内存
const issuedPin = ref('')

// 复制反馈（纯展示态，不触碰凭据逻辑）
const copied = ref(false)
let copyTimer: number | undefined

async function copyPin(): Promise<void> {
  try {
    await navigator.clipboard.writeText(issuedPin.value)
    copied.value = true
    window.clearTimeout(copyTimer)
    copyTimer = window.setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    // 剪贴板不可用（非安全上下文等）：凭证卡内数字 user-select:all，可手动选中复制
  }
}

onUnmounted(() => {
  window.clearTimeout(copyTimer)
})

async function submit(): Promise<void> {
  if (!name.value.trim()) {
    errorMessage.value = '请输入平台运营者名字'
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
    <section class="plate" data-test="onboarding-card">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>

      <template v-if="!issuedPin">
        <h1 class="title">欢迎使用 FamilyGraph</h1>
        <p class="desc">首次启动，请创建平台运营者账号。系统将生成一次性随机 PIN 码。</p>

        <NForm class="form" label-placement="top" :show-feedback="false" @submit.prevent="submit">
          <NFormItem label="平台运营者名字" :label-props="{ for: 'onboarding-name-input' }">
            <NInput
              v-model:value="name"
              placeholder="例如：族长"
              :input-props="{ id: 'onboarding-name-input' }"
              data-test="onboarding-name"
            />
          </NFormItem>
          <p v-if="errorMessage" class="error" role="alert" data-test="onboarding-error">
            {{ errorMessage }}
          </p>
          <NButton
            class="submit"
            type="primary"
            block
            :loading="submitting"
            data-test="onboarding-submit"
            @click="submit"
          >
            创建平台运营者账号
          </NButton>
        </NForm>
      </template>

      <template v-else>
        <h1 class="title">请立即保存 PIN 码</h1>

        <!-- 警示说明：票据式虚线框（proposed/警示色阶） -->
        <div class="warn-strip" role="alert">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
            <path d="M12 2 1 21h22L12 2zm1 14h-2v2h2v-2zm0-7h-2v5h2V9z" />
          </svg>
          <span>该 PIN 码仅显示这一次，关闭后不可回看。请截图或抄写保存。</span>
        </div>

        <!-- 一次性凭证卡：大号等宽数字 + 复制（纸墨主题带朱砂印章） -->
        <div class="pin-voucher">
          <span class="pin-seal" aria-hidden="true">凭</span>
          <span class="voucher-label">一次性 PIN 码</span>
          <div class="pin-display" data-test="one-time-pin">{{ issuedPin }}</div>
          <NButton
            class="copy-btn"
            size="small"
            quaternary
            :class="{ 'is-copied': copied }"
            :aria-label="copied ? '已复制一次性 PIN 码' : '复制一次性 PIN 码'"
            @click="copyPin"
          >
            <template #icon>
              <svg
                v-if="!copied"
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="currentColor"
                aria-hidden="true"
              >
                <path
                  d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z"
                />
              </svg>
              <svg
                v-else
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="currentColor"
                aria-hidden="true"
              >
                <path d="M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
              </svg>
            </template>
            {{ copied ? '已复制' : '复制' }}
          </NButton>
        </div>

        <p class="admin-name">平台运营者：{{ name }}</p>
        <NButton class="submit" type="primary" block data-test="onboarding-done" @click="goLogin">
          我已保存，去登录
        </NButton>
      </template>
    </section>
  </main>
</template>

<style scoped>
.onboarding-view {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

/* 与登录页同族的"名牌"卡基座（token 驱动双主题观感） */
.plate {
  position: relative;
  width: min(440px, 100%);
  padding: 36px 36px 32px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
}

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
  margin: 12px 0 8px;
  text-align: center;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg-ink);
}

.desc {
  margin: 0 0 20px;
  text-align: center;
  font-size: 14px;
  line-height: 1.6;
  color: var(--fg-ink-secondary);
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

.warn-strip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 8px 0 0;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  text-align: left;
  color: var(--fg-status-proposed);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px dashed color-mix(in srgb, var(--fg-status-proposed) 55%, transparent);
  border-radius: var(--fg-radius-control);
}

.warn-strip svg {
  flex-shrink: 0;
  margin-top: 3px;
}

/* 凭证卡：下沉纸面 + 虚线票据边框 */
.pin-voucher {
  position: relative;
  margin: 20px 0 12px;
  padding: 18px 16px 16px;
  text-align: center;
  background-color: var(--fg-surface-sunken);
  border: 1px dashed var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
}

/* 纸墨专属：朱砂"凭"字印章（清雅不渲染） */
.pin-seal {
  display: none;
}

[data-theme='paper'] .pin-seal {
  position: absolute;
  top: -12px;
  right: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  color: var(--fg-accent);
  background-color: var(--fg-surface-raised);
  border: 2px solid var(--fg-accent);
  border-radius: var(--fg-radius-control);
  font-family: var(--fg-font-display);
  font-size: 18px;
  font-weight: 700;
  transform: rotate(8deg);
  opacity: 0.85;
  pointer-events: none;
}

.voucher-label {
  display: block;
  margin-bottom: 8px;
  font-size: 12px;
  letter-spacing: 0.2em;
  color: var(--fg-ink-faint);
}

.pin-display {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'Courier New', monospace;
  font-size: 38px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 8px;
  padding-left: 8px; /* 抵消末字符字距，保持视觉居中 */
  color: var(--fg-ink);
  user-select: all;
}

.copy-btn {
  margin-top: 10px;
  color: var(--fg-ink-secondary);
}

.copy-btn.is-copied {
  color: var(--fg-status-confirmed);
}

.admin-name {
  margin: 0 0 16px;
  text-align: center;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}
</style>
