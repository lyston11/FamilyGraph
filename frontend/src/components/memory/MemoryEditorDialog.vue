<script lang="ts">
/** 编辑器初始值：新增记忆（空）与检索结果「保存」（预填原文）共用 */
export interface MemoryEditorInitial {
  raw_quote?: string
  summary?: string
  suggested_scope?: MemoryScopeKind
}
</script>

<script setup lang="ts">
// 记忆候选编辑器（PRD §2.5「新增」/「保存只能新建候选」）：
// 私有记忆的新增与检索结果「保存」共用本编辑器——提交只创建候选
// （POST /memory-candidates 既有合同），进入「待确认」流程后由用户明确
// scope 才成为可检索记忆；不提供绕过审计的直接发布。
// 服务端真源：创建成功后由 store 重读候选列表（无乐观插入）。
import { NAlert, NButton, NForm, NFormItem, NInput, NModal, NSelect, useMessage } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed, reactive, ref, watch } from 'vue'

import { ApiError } from '@/api/errors'
import { friendlyMemoryError } from '@/api/memory'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import {
  MEMORY_SCOPE_LABELS,
  MEMORY_SENSITIVITY_LABELS,
  type MemoryScopeKind,
  type MemorySensitivity,
} from '@/types/memory'

const props = defineProps<{ show: boolean; initial?: MemoryEditorInitial | null }>()
const emit = defineEmits<{ (event: 'update:show', value: boolean): void }>()

const memory = useMemoryStore()
const spaces = useSpacesStore()
const message = useMessage()

const form = reactive({
  raw_quote: '',
  summary: '',
  purpose: '',
  sensitivity: 'normal' as MemorySensitivity,
  suggested_scope: 'private' as MemoryScopeKind,
})
const saving = ref(false)

const sensitivityOptions = computed<SelectOption[]>(() =>
  (Object.keys(MEMORY_SENSITIVITY_LABELS) as MemorySensitivity[]).map((value) => ({
    value,
    label: MEMORY_SENSITIVITY_LABELS[value],
  })),
)

const scopeKindOptions = computed<SelectOption[]>(() => {
  const options: SelectOption[] = [{ value: 'private', label: MEMORY_SCOPE_LABELS.private }]
  if (spaces.currentSpace) {
    options.push(
      { value: 'household', label: `${MEMORY_SCOPE_LABELS.household}（确认时选择目标空间）` },
      { value: 'lineage', label: `${MEMORY_SCOPE_LABELS.lineage}（确认时选择目标空间）` },
    )
  }
  return options
})

watch(
  () => props.show,
  (show) => {
    if (!show) return
    // 每次打开按初始值重置：最小披露默认 private（V2.5 合同）
    form.raw_quote = props.initial?.raw_quote ?? ''
    form.summary = props.initial?.summary ?? ''
    form.purpose = ''
    form.sensitivity = 'normal'
    form.suggested_scope = props.initial?.suggested_scope ?? 'private'
  },
)

const canSubmit = computed(
  () =>
    form.raw_quote.trim().length > 0 &&
    form.summary.trim().length > 0 &&
    form.purpose.trim().length > 0,
)

function close(): void {
  if (!saving.value) emit('update:show', false)
}

async function submit(): Promise<void> {
  if (!canSubmit.value || saving.value) return
  saving.value = true
  try {
    await memory.createCandidate({
      raw_quote: form.raw_quote.trim(),
      summary: form.summary.trim(),
      purpose: form.purpose.trim().slice(0, 120),
      suggested_scope: form.suggested_scope,
      sensitivity: form.sensitivity,
    })
    message.success('已创建候选，请在「待确认」中确认保存范围')
    emit('update:show', false)
  } catch (reason) {
    // 服务端拒绝不静默：保留可解释错误（quality-guidelines.md V2.5）
    message.error(
      reason instanceof ApiError
        ? friendlyMemoryError(reason.code, reason.message)
        : '创建候选失败，请稍后重试',
    )
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="新增记忆候选"
    data-test="memory-editor-dialog"
    @update:show="(open: boolean) => !open && close()"
  >
    <NAlert
      type="info"
      :show-icon="true"
      :closable="false"
      class="editor-hint"
      data-test="memory-editor-hint"
    >
      保存的内容会先进入「待确认」候选，不会立即进入知识检索；你确认保存范围后才生效。
    </NAlert>
    <NForm class="editor-form" :show-feedback="false" @submit.prevent="submit">
      <NFormItem label="原话内容" :label-props="{ for: 'memory-editor-quote' }">
        <NInput
          v-model:value="form.raw_quote"
          type="textarea"
          :rows="3"
          placeholder="要记住的内容原文"
          :input-props="{ id: 'memory-editor-quote' }"
          data-test="memory-editor-quote"
        />
      </NFormItem>
      <NFormItem label="摘要" :label-props="{ for: 'memory-editor-summary' }">
        <NInput
          v-model:value="form.summary"
          placeholder="一句话摘要"
          :input-props="{ id: 'memory-editor-summary' }"
          data-test="memory-editor-summary"
        />
      </NFormItem>
      <NFormItem label="用途" :label-props="{ for: 'memory-editor-purpose' }">
        <NInput
          v-model:value="form.purpose"
          placeholder="这条记忆的用途（必填，最多 120 字）"
          :input-props="{ id: 'memory-editor-purpose' }"
          data-test="memory-editor-purpose"
        />
      </NFormItem>
      <NFormItem label="敏感等级">
        <NSelect
          v-model:value="form.sensitivity"
          :options="sensitivityOptions"
          data-test="memory-editor-sensitivity"
          aria-label="敏感等级"
        />
      </NFormItem>
      <NFormItem label="建议范围">
        <NSelect
          v-model:value="form.suggested_scope"
          :options="scopeKindOptions"
          data-test="memory-editor-scope"
          aria-label="建议保存范围"
        />
      </NFormItem>
    </NForm>
    <template #footer>
      <div class="modal-actions">
        <NButton quaternary :disabled="saving" data-test="memory-editor-cancel" @click="close">
          取消
        </NButton>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="!canSubmit"
          data-test="memory-editor-submit"
          @click="submit"
        >
          创建候选
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.editor-hint {
  margin-bottom: 12px;
}

.editor-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
