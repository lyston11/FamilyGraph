<script lang="ts">
/** 编辑器初始值：新增记忆（空）与检索结果「保存」（预填原文）共用 */
export interface MemoryEditorInitial {
  source: MemoryCandidateSource
  raw_quote?: string
  summary?: string
  suggested_scope?: MemoryScopeKind
  sensitivity?: MemorySensitivity
  allowed_scopes?: MemoryScope[]
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
  type MemoryCandidateSource,
  type MemoryScope,
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
const source = ref<MemoryCandidateSource>({ kind: 'manual' })
const quoteReadonly = computed(() => source.value.kind !== 'manual')
const submitError = ref<string | null>(null)
let lastSubmission: { fingerprint: string; key: string } | null = null

const sensitivityOptions = computed<SelectOption[]>(() =>
  (Object.keys(MEMORY_SENSITIVITY_LABELS) as MemorySensitivity[]).map((value) => ({
    value,
    label: MEMORY_SENSITIVITY_LABELS[value],
  })),
)

const scopeKindOptions = computed<SelectOption[]>(() => {
  const allowedScopes = source.value.kind === 'rag_chunk' ? props.initial?.allowed_scopes ?? [] : null
  const options: SelectOption[] = [{
    value: 'private',
    label: MEMORY_SCOPE_LABELS.private,
    disabled: allowedScopes !== null && !allowedScopes.includes('private'),
  }]
  const space = spaces.currentSpace
  if (space && (!allowedScopes || allowedScopes.includes(`${space.kind}:${space.id}`))) {
    options.push({ value: space.kind, label: `${MEMORY_SCOPE_LABELS[space.kind]}（${space.name}）` })
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
    form.sensitivity = props.initial?.sensitivity ?? 'normal'
    form.suggested_scope = props.initial?.suggested_scope ?? 'private'
    source.value = props.initial ? { ...props.initial.source } : { kind: 'manual' }
    submitError.value = null
    lastSubmission = null
  },
  { immediate: true },
)

const canSubmit = computed(
  () =>
    memory.memoryEnabled &&
    (source.value.kind !== 'rag_chunk' || source.value.space_id === spaces.currentSpaceId) &&
    scopeKindOptions.value.some((option) => option.value === form.suggested_scope && !option.disabled) &&
    form.raw_quote.trim().length > 0 &&
    form.summary.trim().length > 0 &&
    form.purpose.trim().length > 0 && form.purpose.trim().length <= 120,
)

function close(): void {
  if (!saving.value) emit('update:show', false)
}

async function submit(): Promise<void> {
  if (!canSubmit.value || saving.value) return
  const payload = {
    source: { ...source.value },
    // Non-manual quotes are read from the precise source by the server.
    ...(source.value.kind === 'manual' ? { raw_quote: form.raw_quote.trim() } : {}),
    summary: form.summary.trim(),
    purpose: form.purpose.trim(),
    suggested_scope: form.suggested_scope,
    sensitivity: form.sensitivity,
  }
  const fingerprint = JSON.stringify(payload)
  if (lastSubmission?.fingerprint !== fingerprint) {
    lastSubmission = { fingerprint, key: crypto.randomUUID() }
  }
  saving.value = true
  submitError.value = null
  try {
    await memory.createCandidate({
      ...payload,
      idempotency_key: lastSubmission.key,
    })
    message.success('已创建候选，请在「待确认」中确认保存范围')
    emit('update:show', false)
  } catch (reason) {
    // 服务端拒绝不静默：保留可解释错误（quality-guidelines.md V2.5）
    submitError.value = reason instanceof ApiError
      ? friendlyMemoryError(reason.code, reason.message)
      : '创建候选失败，请稍后重试'
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
    <NAlert v-if="quoteReadonly" type="info" :closable="false" class="editor-hint" data-test="memory-editor-source-hint">
      原文来自当前获权来源，只读保存；摘要和用途由你整理，保存后仍受原来源权限约束。
    </NAlert>
    <NAlert v-if="!memory.memoryEnabled" type="warning" :closable="false" class="editor-hint" data-test="memory-editor-disabled">
      {{ memory.featureStateKnown ? '记忆功能当前未启用，暂时不能保存。' : '能力状态暂时无法确认，请刷新后重试。' }}
    </NAlert>
    <NAlert v-if="submitError" type="error" :closable="false" class="editor-hint" data-test="memory-editor-error">
      {{ submitError }}
    </NAlert>
    <NForm class="editor-form" :show-feedback="false" @submit.prevent="submit">
      <NFormItem label="原话内容" :label-props="{ for: 'memory-editor-quote' }">
        <NInput
          v-model:value="form.raw_quote"
          type="textarea"
          :rows="3"
          :readonly="quoteReadonly"
          :disabled="saving"
          placeholder="要记住的内容原文"
          :input-props="{ id: 'memory-editor-quote' }"
          data-test="memory-editor-quote"
        />
      </NFormItem>
      <NFormItem label="摘要" :label-props="{ for: 'memory-editor-summary' }">
        <NInput
          v-model:value="form.summary"
          :disabled="saving"
          placeholder="一句话摘要"
          :input-props="{ id: 'memory-editor-summary' }"
          data-test="memory-editor-summary"
        />
      </NFormItem>
      <NFormItem label="用途" :label-props="{ for: 'memory-editor-purpose' }">
        <NInput
          v-model:value="form.purpose"
          :maxlength="120"
          :disabled="saving"
          placeholder="这条记忆的用途（必填，最多 120 字）"
          :input-props="{ id: 'memory-editor-purpose' }"
          data-test="memory-editor-purpose"
        />
      </NFormItem>
      <NFormItem label="敏感等级">
        <NSelect
          v-model:value="form.sensitivity"
          :options="sensitivityOptions"
          :disabled="saving || quoteReadonly"
          data-test="memory-editor-sensitivity"
          aria-label="敏感等级"
        />
      </NFormItem>
      <NFormItem label="建议范围">
        <NSelect
          v-model:value="form.suggested_scope"
          :options="scopeKindOptions"
          :disabled="saving"
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
          :disabled="!canSubmit || saving"
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
<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度（非 scoped 必需）。
 * naive 的 preset="card" 没有内置宽度上限（card 的 width:100% 会取到视口宽），
 * 所以每个卡片弹窗都要在这里给出宽度；width: min(Npx, calc(100vw - 48px))
 * 在窄屏保留 24px 双侧留白。见 spec/frontend/component-guidelines.md。 */
[data-test='memory-editor-dialog'] {
  width: min(520px, calc(100vw - 48px));
}
</style>
