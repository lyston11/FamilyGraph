<script setup lang="ts">
/**
 * 审批二次确认弹窗（后台唯一业务写 UI 的确认层）。
 *
 * - approve：理由可选；reject：理由必填；
 * - 明确展示"终态不可改判"不可逆提示；
 * - 确认按钮即后端 confirm: true 的前端对应物；Escape 关闭不提交。
 */
import { computed, ref, watch } from 'vue'
import { NModal } from 'naive-ui'

const props = defineProps<{
  visible: boolean
  decision: 'approve' | 'reject'
  applicantName: string
  spaceName: string
  pending?: boolean
  /** 上次提交失败的安全文案（服务端 422/409 统一文案）。 */
  errorMessage?: string | null
}>()

const emit = defineEmits<{
  confirm: [note: string | null]
  close: []
}>()

const note = ref('')
const touched = ref(false)

const isReject = computed(() => props.decision === 'reject')

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      note.value = ''
      touched.value = false
    }
  },
)

const validationMessage = computed(() => {
  if (!isReject.value) return null
  if (!note.value.trim()) return '驳回必须填写理由'
  return null
})

const canConfirm = computed(() => {
  if (props.pending) return false
  return validationMessage.value === null
})

const title = computed(() => (isReject.value ? '驳回空间管理员申请' : '批准空间管理员申请'))

function onConfirm(): void {
  touched.value = true
  if (!canConfirm.value) return
  emit('confirm', isReject.value ? note.value.trim() : note.value.trim() || null)
}
</script>

<template>
  <NModal
    :show="visible"
    :mask-closable="false"
    :aria-label="title"
    @update:show="(value: boolean) => !value && emit('close')"
  >
    <div class="ag-card ag-modal-card-lg" role="dialog" aria-modal="true">
      <h2 class="ag-page-title ag-heading-modal">{{ title }}</h2>
      <p class="ag-page-subtitle ag-sub-flush">
        申请人：{{ applicantName }} · 目标空间：{{ spaceName }}
      </p>
      <div class="sensitive-notice" role="alert">
        裁决提交后立即生效且为终态：不可撤销、不可改判。请确认已核对申请人资格与目标空间。
      </div>
      <div class="form-field">
        <label for="decision-note-input">{{ isReject ? '驳回理由（必填）' : '备注（可选）' }}</label>
        <textarea
          id="decision-note-input"
          v-model="note"
          rows="3"
          maxlength="1000"
          @blur="touched = true"
        ></textarea>
        <span v-if="touched && validationMessage" class="form-error" role="alert">
          {{ validationMessage }}
        </span>
        <span v-if="errorMessage" class="form-error" role="alert" data-testid="decision-error">
          {{ errorMessage }}
        </span>
      </div>
      <div class="form-actions">
        <button type="button" class="ag-tag" data-testid="decision-cancel" @click="emit('close')">
          取消
        </button>
        <button
          type="button"
          class="ag-tag"
          :class="isReject ? 'ag-btn-danger' : 'ag-btn-primary'"
          :disabled="!canConfirm"
          data-testid="decision-confirm"
          @click="onConfirm"
        >
          {{ pending ? '提交中…' : '确认裁决（不可逆）' }}
        </button>
      </div>
    </div>
  </NModal>
</template>
