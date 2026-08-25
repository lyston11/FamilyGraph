<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'

/**
 * 改 PIN 表单：SettingsView 与强制改 PIN 页共用。
 * 成功后服务端使全部会话失效 → 跳登录页重新登录。
 */
const emit = defineEmits<{ changed: [] }>()

const auth = useAuthStore()

const form = reactive({ oldPin: '', newPin: '', confirmPin: '' })
const submitting = ref(false)
const errorMessage = ref('')

async function submit(): Promise<void> {
  if (!/^\d{6}$/.test(form.oldPin) || !/^\d{6}$/.test(form.newPin)) {
    errorMessage.value = 'PIN 码须为 6 位数字'
    return
  }
  if (form.newPin !== form.confirmPin) {
    errorMessage.value = '两次输入的新 PIN 码不一致'
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    await auth.changePin(form.oldPin, form.newPin)
    ElMessage.success('PIN 码已修改，请用新 PIN 码重新登录')
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '修改失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-form label-position="top" data-test="change-pin-form" @submit.prevent="submit">
    <el-form-item label="当前 PIN 码">
      <el-input
        v-model="form.oldPin"
        type="password"
        inputmode="numeric"
        maxlength="6"
        show-password
        data-test="old-pin"
      />
    </el-form-item>
    <el-form-item label="新 PIN 码（6 位数字）">
      <el-input
        v-model="form.newPin"
        type="password"
        inputmode="numeric"
        maxlength="6"
        show-password
        data-test="new-pin"
      />
    </el-form-item>
    <el-form-item label="确认新 PIN 码">
      <el-input
        v-model="form.confirmPin"
        type="password"
        inputmode="numeric"
        maxlength="6"
        show-password
        data-test="confirm-pin"
      />
    </el-form-item>

    <p v-if="errorMessage" class="error" data-test="change-pin-error">{{ errorMessage }}</p>

    <el-button type="primary" :loading="submitting" data-test="change-pin-submit" @click="submit">
      修改 PIN 码
    </el-button>
  </el-form>
</template>

<style scoped>
.error {
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
