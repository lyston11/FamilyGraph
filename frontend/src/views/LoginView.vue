<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import type { ChallengeCandidate } from '@/types/api'
import { useAuthStore } from '@/stores/auth'

/**
 * 登录页：名字 + PIN（密码态输入）。
 * 同名同 PIN 撞车时后端返回 409 challenge，弹候选选择列表消歧（HANDOFF A1/A2）。
 */
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

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
    ElMessage.success('登录成功')
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
    ElMessage.success('登录成功')
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
    <el-card class="login-card">
      <h1 class="title">FamilyGraph</h1>
      <p class="subtitle">名字 + PIN 码登录</p>

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="名字">
          <el-input v-model="form.name" placeholder="请输入名字" data-test="login-name" />
        </el-form-item>
        <el-form-item label="PIN 码">
          <el-input
            v-model="form.pin"
            type="password"
            inputmode="numeric"
            maxlength="6"
            show-password
            placeholder="6 位数字 PIN 码"
            data-test="login-pin"
            @keyup.enter="submit"
          />
        </el-form-item>

        <p v-if="errorMessage" class="error" data-test="login-error">{{ errorMessage }}</p>

        <el-button
          type="primary"
          class="submit"
          :loading="submitting"
          data-test="login-submit"
          @click="submit"
        >
          登录
        </el-button>
      </el-form>
    </el-card>

    <!-- 同名同 PIN 候选选择弹窗 -->
    <el-dialog
      v-model="challengeVisible"
      title="存在同名账号"
      width="360px"
      align-center
      data-test="challenge-dialog"
    >
      <p class="hint">检测到多个同名且 PIN 匹配的账号，请选择你的账号：</p>
      <el-radio-group v-model="selectedUserId" class="candidate-list">
        <el-radio
          v-for="(candidate, index) in candidates"
          :key="candidate.id"
          :value="candidate.id"
          border
          :data-test="`challenge-candidate-${index}`"
        >
          {{ candidate.name }}（ID: {{ candidate.id }}）
        </el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="challengeVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="selectedUserId === null"
          data-test="challenge-confirm"
          @click="confirmCandidate"
        >
          继续
        </el-button>
      </template>
    </el-dialog>
  </main>
</template>

<style scoped>
.login-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.login-card {
  width: 360px;
}

.title {
  margin: 0;
  text-align: center;
  font-size: 24px;
}

.subtitle {
  margin: 8px 0 20px;
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

.hint {
  margin-top: 0;
  color: var(--el-text-color-secondary);
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
