<script setup lang="ts">
import { reactive, ref } from 'vue'
import { NButton, NForm, NFormItem, NInput, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'

/**
 * 改 PIN 表单：SettingsView 与强制改 PIN 页共用。
 * 成功后服务端使全部会话失效 → 跳登录页重新登录。
 */
const emit = defineEmits<{ changed: [] }>()

const auth = useAuthStore()
const message = useMessage()

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
    message.success('PIN 码已修改，请用新 PIN 码重新登录')
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '修改失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <NForm
    class="form"
    label-placement="top"
    :show-feedback="false"
    data-test="change-pin-form"
    @submit.prevent="submit"
  >
    <NFormItem label="当前 PIN 码" :label-props="{ for: 'old-pin-input' }">
      <NInput
        v-model:value="form.oldPin"
        type="password"
        show-password-on="click"
        :maxlength="6"
        :input-props="{ id: 'old-pin-input', inputmode: 'numeric' }"
        data-test="old-pin"
      />
    </NFormItem>
    <NFormItem label="新 PIN 码（6 位数字）" :label-props="{ for: 'new-pin-input' }">
      <NInput
        v-model:value="form.newPin"
        type="password"
        show-password-on="click"
        :maxlength="6"
        :input-props="{ id: 'new-pin-input', inputmode: 'numeric' }"
        data-test="new-pin"
      />
    </NFormItem>
    <NFormItem label="确认新 PIN 码" :label-props="{ for: 'confirm-pin-input' }">
      <NInput
        v-model:value="form.confirmPin"
        type="password"
        show-password-on="click"
        :maxlength="6"
        :input-props="{ id: 'confirm-pin-input', inputmode: 'numeric' }"
        data-test="confirm-pin"
      />
    </NFormItem>

    <p v-if="errorMessage" class="error" role="alert" data-test="change-pin-error">
      {{ errorMessage }}
    </p>

    <NButton
      class="submit"
      type="primary"
      block
      :loading="submitting"
      data-test="change-pin-submit"
      @click="submit"
    >
      修改 PIN 码
    </NButton>
  </NForm>
</template>

<style scoped>
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
