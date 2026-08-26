<script setup lang="ts">
import { computed, ref } from 'vue'

/**
 * Composer（PRD AS-3）：Enter 发送、Shift+Enter 换行；发送中禁用；
 * 取消按钮仅作用于当前 active run。中文输入法组词态（isComposing）不触发发送。
 */
const props = defineProps<{
  modelValue: string
  sending: boolean
  /** 是否存在可取消的 active run */
  canCancel: boolean
  cancelling?: boolean
  maxLength?: number
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string]; send: []; cancel: [] }>()

const inputEl = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(
  () => !props.sending && props.modelValue.trim().length > 0,
)

function onInput(value: string): void {
  emit('update:modelValue', value)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter') return
  if (event.shiftKey || event.isComposing) return // Shift+Enter 换行；IME 组词不发送
  event.preventDefault()
  if (canSend.value) emit('send')
}

defineExpose({ focus: () => inputEl.value?.focus() })
</script>

<template>
  <div class="composer" data-test="composer">
    <textarea
      ref="inputEl"
      class="input"
      :value="modelValue"
      rows="2"
      :maxlength="maxLength ?? 8000"
      placeholder="问问这个空间里的家谱问题…（Enter 发送，Shift+Enter 换行）"
      aria-label="输入消息"
      data-test="composer-input"
      :disabled="sending"
      @input="onInput(($event.target as HTMLTextAreaElement).value)"
      @keydown="onKeydown"
    />
    <div class="actions">
      <span v-if="maxLength" class="counter" aria-hidden="true">
        {{ modelValue.length }}/{{ maxLength }}
      </span>
      <el-button
        v-if="canCancel"
        size="small"
        type="warning"
        plain
        data-test="cancel-run-btn"
        :disabled="cancelling === true"
        @click="emit('cancel')"
      >
        取消回答
      </el-button>
      <el-button
        type="primary"
        size="small"
        data-test="send-btn"
        :disabled="!canSend"
        @click="emit('send')"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  padding: 10px 14px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid var(--el-border-color-lighter);
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--el-bg-color);
}

.input {
  width: 100%;
  resize: none;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  box-sizing: border-box;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
}

.input:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}

.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.counter {
  margin-right: auto;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
