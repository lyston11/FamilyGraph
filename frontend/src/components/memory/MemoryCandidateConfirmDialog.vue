<script setup lang="ts">
// 候选确认弹层（PRD §2.5 / design §5.4，从 MemoryManager 抽出）：
// 确认前完整展示原话、摘要、用途、敏感等级、目标 scope 与隐私影响；
// 只提供「确认保存 / 忽略（拒绝）/ 取消（稍后处理）」，无绕过审计的直接发布。
// store 负责服务端真源：确认/忽略成功后由 store 重读服务端状态（无乐观副本）。
import { NAlert, NButton, NInputNumber, NModal, NSelect, useMessage } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import { ApiError } from '@/api/errors'
import { friendlyMemoryError } from '@/api/memory'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import {
  MEMORY_SCOPE_LABELS,
  MEMORY_SENSITIVITY_LABELS,
  MEMORY_SOURCE_STATUS_LABELS,
  memorySourceReadable,
  type MemoryCandidate,
  type MemoryScope,
  type MemorySensitivity,
} from '@/types/memory'

const props = defineProps<{ candidate: MemoryCandidate | null }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const memory = useMemoryStore()
const spaces = useSpacesStore()
const message = useMessage()

const selectedScope = ref<MemoryScope>('private')
const retentionDays = ref<number | null>(null)
const saving = ref(false)
const actionError = ref<string | null>(null)

watch(
  () => props.candidate?.id,
  () => {
    // 每次打开重置为最小披露默认：private（V2.5 合同：默认最小 scope）
    selectedScope.value = 'private'
    retentionDays.value = null
    actionError.value = null
  },
)

/** 高敏感/必须本地处理的候选不允许共享（服务端同规则，前端 fail-closed 置灰） */
const sharedDisabled = computed(
  () =>
    props.candidate !== null &&
    (props.candidate.sensitivity === 'high' || props.candidate.sensitivity === 'local_required'),
)

const scopeOptions = computed<SelectOption[]>(() => {
  const allowedScopes = props.candidate?.allowed_scopes ?? []
  const options: SelectOption[] = [
    { value: 'private', label: MEMORY_SCOPE_LABELS.private, disabled: !allowedScopes.includes('private') },
  ]
  const space = spaces.currentSpace
  if (space) {
    const scope: MemoryScope = `${space.kind}:${space.id}`
    options.push({
      value: scope,
      label: `${space.name} · ${MEMORY_SCOPE_LABELS[space.kind]}`,
      disabled: sharedDisabled.value || !allowedScopes.includes(scope),
    })
  }
  return options
})

const canConfirm = computed(() =>
  memory.memoryEnabled && !saving.value && props.candidate?.status === 'pending' &&
  props.candidate.source_status === 'available' &&
  scopeOptions.value.some((option) => option.value === selectedScope.value && !option.disabled),
)

/** 隐私影响说明随目标 scope 变化（PRD §2.5：确认前可见） */
const privacyImpact = computed(() => {
  if (sharedDisabled.value) {
    return `${MEMORY_SENSITIVITY_LABELS[props.candidate?.sensitivity ?? 'normal']}内容只能保存在「仅我可见」范围，任何共享都会被服务端拒绝。`
  }
  if (selectedScope.value === 'private') {
    const dependencyHint = props.candidate?.source_kind === 'rag_chunk'
      ? '此副本仍受原来源权限约束；原来源失效时不再显示内容。'
      : ''
    return `仅本人可见：只有你自己在 Assistant 中可检索这条内容，不进入家庭或家族共享。${dependencyHint}`
  }
  if (selectedScope.value.startsWith('household:')) {
    return '家庭共享：确认后当前家庭空间的授权成员可在知识检索中引用这条内容。'
  }
  return '族谱共享：确认后当前家族空间的授权成员可在知识检索中引用这条内容。'
})

function onScopeSelect(value: string | number | Array<string | number> | null): void {
  // options 只产出合同内的 scope 字符串（type-safety.md：不改写枚举）
  if (typeof value === 'string' && scopeOptions.value.some((option) => option.value === value && !option.disabled)) {
    selectedScope.value = value as MemoryScope
  }
}

function onRetentionInput(value: number | null): void {
  retentionDays.value = value
}

function close(): void {
  if (!saving.value) emit('close')
}

async function confirm(): Promise<void> {
  const candidate = props.candidate
  if (!candidate || !canConfirm.value) return
  saving.value = true
  actionError.value = null
  try {
    await memory.confirmCandidate(candidate.id, selectedScope.value, retentionDays.value)
    message.success('记忆已确认，并按你选择的范围保存')
    emit('close')
  } catch (reason) {
    // Policy Guard / 服务端拒绝不静默：保留可解释错误（quality-guidelines.md V2.5）
    actionError.value = reason instanceof ApiError
      ? friendlyMemoryError(reason.code, reason.message)
      : '确认失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

async function dismiss(): Promise<void> {
  const candidate = props.candidate
  if (!candidate || !memory.memoryEnabled || saving.value) return
  saving.value = true
  actionError.value = null
  try {
    await memory.dismissCandidate(candidate.id)
    message.success('候选记忆已忽略')
    emit('close')
  } catch (reason) {
    actionError.value = reason instanceof ApiError
      ? friendlyMemoryError(reason.code, reason.message)
      : '操作失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

/** 敏感等级徽章阶（design.md §3.4）：normal=confirmed / 敏感系=proposed / 高危=disputed */
function sensitivityBadge(sensitivity: MemorySensitivity): string {
  if (sensitivity === 'normal') return 'fg-badge--confirmed'
  if (sensitivity === 'high' || sensitivity === 'local_required') return 'fg-badge--disputed'
  return 'fg-badge--proposed'
}
</script>

<template>
  <NModal
    :show="candidate !== null"
    preset="card"
    title="确认一条记忆"
    data-test="confirm-memory-dialog"
    @update:show="(open: boolean) => !open && close()"
  >
    <template v-if="candidate">
      <div class="dialog-topline">
        <!-- V2.5 合同：确认时敏感等级必须可见（icon+文字，不依赖列表卡） -->
        <span
          class="fg-badge"
          :class="sensitivityBadge(candidate.sensitivity)"
          data-test="confirm-memory-sensitivity"
        >
          <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor" aria-hidden="true">
            <path d="M12 2a5 5 0 0 0-5 5v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2h-1V7a5 5 0 0 0-5-5Zm-3 8V7a3 3 0 1 1 6 0v3H9Z" />
          </svg>
          敏感等级：{{ MEMORY_SENSITIVITY_LABELS[candidate.sensitivity] }}
        </span>
        <span class="fg-badge fg-badge--proposed">候选 · 未进入检索</span>
      </div>
      <NAlert v-if="candidate.source_status !== 'available'" type="warning" :closable="false" class="privacy-impact" data-test="confirm-memory-source-status">
        {{ MEMORY_SOURCE_STATUS_LABELS[candidate.source_status] }}
      </NAlert>
      <NAlert v-if="!memory.memoryEnabled" type="warning" :closable="false" class="privacy-impact" data-test="confirm-memory-disabled">
        {{ memory.featureStateKnown ? '记忆功能当前未启用，暂时不能处理候选。' : '能力状态暂时无法确认，请刷新后重试。' }}
      </NAlert>
      <NAlert v-if="actionError" type="error" :closable="false" class="privacy-impact" data-test="confirm-memory-error">
        {{ actionError }}
      </NAlert>
      <template v-if="memorySourceReadable(candidate.source_status)">
        <p class="dialog-summary">{{ candidate.summary }}</p>
        <blockquote>“{{ candidate.raw_quote }}”</blockquote>
      </template>
      <div class="dialog-fields">
        <span v-if="memorySourceReadable(candidate.source_status)" class="meta-line">用途：{{ candidate.purpose }}</span>
        <label class="field-label">保存范围</label>
        <NSelect
          :value="selectedScope"
          :options="scopeOptions"
          :consistent-menu-width="false"
          :disabled="saving || !memory.memoryEnabled || candidate.source_status !== 'available'"
          data-test="memory-scope-select"
          aria-label="选择保存范围"
          @update:value="onScopeSelect"
        />
        <label class="field-label">保留期限（可选）</label>
        <div class="retention-row">
          <NInputNumber
            :value="retentionDays"
            :min="1"
            :max="3650"
            :disabled="saving || !memory.memoryEnabled || candidate.source_status !== 'available'"
            placeholder="不填写表示长期保留"
            data-test="memory-retention-days"
            aria-label="保留期限天数"
            @update:value="onRetentionInput"
          />
          <span class="field-hint">天；系统不会自动扩大你选择的范围。</span>
        </div>
      </div>
      <!-- 隐私影响：确认前必须可见（PRD §2.5） -->
      <NAlert
        type="info"
        :show-icon="true"
        :closable="false"
        class="privacy-impact"
        data-test="memory-privacy-impact"
      >
        {{ privacyImpact }}
      </NAlert>
      <NAlert
        v-if="sharedDisabled"
        type="warning"
        :show-icon="true"
        :closable="false"
        class="privacy-impact"
        data-test="memory-sharing-warning"
      >
        {{ MEMORY_SENSITIVITY_LABELS[candidate.sensitivity] }}内容不能共享到空间。
      </NAlert>
    </template>
    <template #footer>
      <div class="modal-actions">
        <!-- 稍后处理：仅关闭弹层，不改变候选状态 -->
        <NButton quaternary data-test="confirm-memory-later" @click="close">稍后处理</NButton>
        <!-- 拒绝：dismiss 候选（服务端命令） -->
        <NButton
          secondary
          :disabled="saving || !memory.memoryEnabled"
          data-test="confirm-memory-dismiss"
          @click="dismiss"
        >
          忽略
        </NButton>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="!canConfirm"
          data-test="confirm-memory-submit"
          @click="confirm"
        >
          确认保存
        </NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.dialog-topline {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
}

.dialog-topline .fg-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.dialog-summary {
  margin: 12px 0 0;
  font-weight: 600;
  line-height: 1.5;
}

blockquote {
  margin: 8px 0;
  padding: 8px 11px;
  border-left: 2px solid color-mix(in srgb, var(--fg-status-confirmed) 45%, transparent);
  background: var(--fg-surface-sunken);
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.meta-line {
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.dialog-fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 12px 0;
}

.field-label {
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.retention-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.field-hint {
  display: block;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.privacy-impact {
  margin-top: 4px;
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
[data-test='confirm-memory-dialog'] {
  width: min(460px, calc(100vw - 48px));
}
</style>
