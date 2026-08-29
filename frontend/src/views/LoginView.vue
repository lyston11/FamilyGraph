<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NForm, NFormItem, NInput, NModal, NRadio, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import type { ChallengeCandidate } from '@/types/api'
import { useAuthStore } from '@/stores/auth'

/**
 * 登录页（沉浸页，meta.chrome='blank'）：名字 + PIN（密码态输入）。
 * 同名同 PIN 撞车时后端返回 409 challenge，弹候选选择列表消歧（HANDOFF A1/A2）。
 * 视觉：居中"名牌"卡——纸墨主题宣纸底双线框 + 朱砂印章 + 宋体标题；清雅主题白底圆角卡。
 */
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const message = useMessage()

const form = reactive({ name: '', pin: '' })
const submitting = ref(false)
const errorMessage = ref('')

// ---- 消歧弹窗状态 ----
const challengeVisible = ref(false)
const challengeId = ref('')
const candidates = ref<ChallengeCandidate[]>([])
const selectedUserId = ref<number | null>(null)

function redirectAfterLogin(): void {
  const target = route.query.redirect
  if (auth.mustChangePin) {
    void router.replace({ name: 'force-change-pin' })
    return
  }
  if (typeof target === 'string' && target.startsWith('/')) {
    void router.replace(target)
    return
  }
  void router.replace({ name: 'home' })
}

async function submit(): Promise<void> {
  if (!form.name.trim() || !/^\d{6}$/.test(form.pin)) {
    errorMessage.value = '请输入名字和 6 位数字 PIN 码'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    await auth.login(form.name.trim(), form.pin)
    message.success('登录成功')
    redirectAfterLogin()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      openChallengeDialog(error)
    } else {
      errorMessage.value = error instanceof ApiError ? error.message : '登录失败，请稍后重试'
    }
  } finally {
    submitting.value = false
  }
}

interface ChallengeDetail {
  challenge_id?: string
  candidates?: ChallengeCandidate[]
}

function isChallengeDetail(detail: unknown): detail is ChallengeDetail {
  return typeof detail === 'object' && detail !== null && 'challenge_id' in detail
}

function openChallengeDialog(error: ApiError): void {
  if (!isChallengeDetail(error.detail)) {
    errorMessage.value = error.message
    return
  }
  challengeId.value = error.detail.challenge_id ?? ''
  candidates.value = error.detail.candidates ?? []
  selectedUserId.value = null
  challengeVisible.value = true
}

async function confirmCandidate(): Promise<void> {
  if (selectedUserId.value === null) return
  try {
    await auth.selectCandidate(challengeId.value, selectedUserId.value)
    message.success('登录成功')
    challengeVisible.value = false
    redirectAfterLogin()
  } catch (error) {
    // 过期/重放：统一提示重新登录，关闭弹窗回到第一步
    challengeVisible.value = false
    errorMessage.value =
      error instanceof ApiError ? error.message : '登录校验已失效，请重新登录'
  }
}
</script>

<template>
  <main class="login-view">
    <section class="plate" aria-labelledby="login-title">
      <div class="brand" aria-hidden="true">
        <span class="seal">谱</span>
      </div>
      <h1 id="login-title" class="title">FamilyGraph</h1>
      <p class="subtitle">名字 + PIN 码登录</p>

      <NForm class="form" label-placement="top" :show-feedback="false" @submit.prevent="submit">
        <NFormItem label="名字" :label-props="{ for: 'login-name-input' }">
          <NInput
            v-model:value="form.name"
            placeholder="请输入名字"
            :input-props="{ id: 'login-name-input' }"
            data-test="login-name"
            @keyup.enter="submit"
          />
        </NFormItem>
        <NFormItem label="PIN 码" :label-props="{ for: 'login-pin-input' }">
          <NInput
            v-model:value="form.pin"
            type="password"
            show-password-on="click"
            :maxlength="6"
            placeholder="6 位数字 PIN 码"
            :input-props="{ id: 'login-pin-input', inputmode: 'numeric' }"
            data-test="login-pin"
            @keyup.enter="submit"
          />
        </NFormItem>

        <p v-if="errorMessage" class="error" role="alert" data-test="login-error">
          {{ errorMessage }}
        </p>

        <NButton
          class="submit"
          type="primary"
          block
          :loading="submitting"
          data-test="login-submit"
          @click="submit"
        >
          登录
        </NButton>
      </NForm>
    </section>

    <!-- 同名同 PIN 候选选择弹窗 -->
    <NModal
      v-model:show="challengeVisible"
      preset="card"
      title="存在同名账号"
      data-test="challenge-dialog"
    >
      <p class="hint">检测到多个同名且 PIN 匹配的账号，请选择你的账号：</p>
      <div class="candidate-list" role="radiogroup" aria-label="同名候选账号">
        <NRadio
          v-for="(candidate, index) in candidates"
          :key="candidate.id"
          class="candidate"
          :class="{ 'is-selected': selectedUserId === candidate.id }"
          :checked="selectedUserId === candidate.id"
          :value="candidate.id"
          :data-test="`challenge-candidate-${index}`"
          @update:checked="
            (checked: boolean) => {
              if (checked) selectedUserId = candidate.id
            }
          "
        >
          {{ candidate.name }}（ID: {{ candidate.id }}）
        </NRadio>
      </div>
      <template #footer>
        <div class="modal-actions">
          <NButton @click="challengeVisible = false">取消</NButton>
          <NButton
            type="primary"
            :disabled="selectedUserId === null"
            data-test="challenge-confirm"
            @click="confirmCandidate"
          >
            继续
          </NButton>
        </div>
      </template>
    </NModal>
  </main>
</template>

<style scoped>
.login-view {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  box-sizing: border-box;
}

/* 名牌卡：纸墨=宣纸浮牌 + 证书双线框；清雅=白底大圆角 + 柔和阴影（观感差异由 token 驱动） */
.plate {
  position: relative;
  width: min(400px, 100%);
  padding: 36px 36px 32px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-raised);
  box-sizing: border-box;
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

/* 朱砂印章式品牌标识（清雅主题为青蓝方块，同 token 派生） */
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
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fg-ink);
}

.subtitle {
  margin: 0 0 24px;
  text-align: center;
  font-size: 14px;
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

.hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

/* 候选行：选中态用主色描边 + 柔和底（域内"点名牌"隐喻） */
.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.candidate-list :deep(.candidate) {
  padding: 10px 12px;
  background-color: var(--fg-surface);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  transition:
    border-color 0.2s,
    background-color 0.2s;
}

.candidate-list :deep(.candidate.is-selected) {
  border-color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body，scoped 选择器不可达：用 data-test 锚定宽度 */
[data-test='challenge-dialog'] {
  width: min(400px, calc(100vw - 48px));
}
</style>
