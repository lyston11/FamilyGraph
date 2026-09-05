<script setup lang="ts">
/**
 * 敏感详情理由弹窗：访问会话申请前必须填写理由。
 * 理由不写 URL / localStorage / sessionStorage；Escape 关闭不提交。
 * 校验与后端 AdminAccessSessionCreate 对齐：非空、≤500、无控制字符。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NModal } from 'naive-ui'

const props = defineProps<{
  visible: boolean
  /** 目标描述（如 "成员档案：李四"、"空间：X 家族"），仅用于展示。 */
  targetLabel: string
  pending?: boolean
  /** 上次申请失败的安全文案（如票据被拒后重新申请）。 */
  errorMessage?: string | null
}>()

const emit = defineEmits<{
  submit: [reason: string]
  close: []
}>()

const reason = ref('')
const touched = ref(false)

const MAX_REASON_LENGTH = 500

const validationMessage = computed(() => {
  const value = reason.value.trim()
  if (!value) return '请填写访问理由'
  if (value.length > MAX_REASON_LENGTH) return `理由不能超过 ${MAX_REASON_LENGTH} 字`
  if (Array.from(value).some((ch) => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) {
    return '理由不能包含控制字符'
  }
  return null
})

const canSubmit = computed(() => validationMessage.value === null && reason.value.trim() !== '')

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      reason.value = ''
      touched.value = false
    }
  },
)

function onSubmit(): void {
  touched.value = true
  if (!canSubmit.value || props.pending) return
  emit('submit', reason.value.trim())
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.visible) {
    emit('close')
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <NModal
    :show="visible"
    :mask-closable="false"
    aria-label="敏感详情访问理由"
    @update:show="(value: boolean) => !value && emit('close')"
  >
    <div class="ag-card ag-modal-card" role="dialog" aria-modal="true">
      <h2 class="ag-page-title ag-heading-modal">申请查看敏感详情</h2>
      <p class="ag-page-subtitle ag-sub-flush">
        目标：{{ targetLabel }}
      </p>
      <div class="sensitive-notice">
        需要说明访问理由并获取 30 分钟访问授权；票据仅保存在本次页面会话中，刷新后需重新申请。
      </div>
      <div class="form-field">
        <label for="access-reason-input">访问理由</label>
        <textarea
          id="access-reason-input"
          v-model="reason"
          rows="3"
          maxlength="500"
          placeholder="例如：处理异常队列中该空间的工单"
          @blur="touched = true"
        ></textarea>
        <span v-if="touched && validationMessage" class="form-error" role="alert">
          {{ validationMessage }}
        </span>
        <span v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</span>
      </div>
      <div class="form-actions">
        <button type="button" class="ag-tag" data-testid="reason-cancel" @click="emit('close')">
          取消
        </button>
        <button
          type="button"
          class="ag-tag ag-btn-primary"
          :disabled="pending"
          data-testid="reason-submit"
          @click="onSubmit"
        >
          {{ pending ? '申请中…' : '提交理由' }}
        </button>
      </div>
    </div>
  </NModal>
</template>
