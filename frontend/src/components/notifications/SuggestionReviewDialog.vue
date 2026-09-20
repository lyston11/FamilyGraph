<script setup lang="ts">
// Steward 建议详情/确认弹层（09-11 candidate-review）：
// - 展示：双方、关系/建议结构化值、证据摘要（fact 计数）、隐私影响提示；
//   只渲染服务端安全显示字段（无 raw model payload，后端不下发）；
// - 动作分离：打开弹层只读（open_details），提交（submit）需用户显式点击
//   且带 confirm=true + 每次生成的 Idempotency-Key；驳回（dismiss）独立按钮；
// - 终态/无权动作由服务端 allowed_actions 决定，前端不本地推导授权；
// - relation_proposal 展示服务端关联提案状态及实际待确认人数；
// - 身份重复/资料缺口：v1 无 submit，仅展示指引（转既有资料/去重流程）。
import { NButton, NModal, NSpin } from 'naive-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { useAuthStore } from '@/stores/auth'
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import { SUGGESTION_KIND_LABELS, SUGGESTION_STATE_LABELS } from '@/types/notifications'
import type { SuggestionItem } from '@/types/api'

const props = defineProps<{ spaceId: number }>()
const opened = defineModel<boolean>('opened', { required: true })
const suggestionModel = defineModel<SuggestionItem | null>('suggestion', { default: null })
const suggestion = computed<SuggestionItem | null>(() => suggestionModel.value ?? null)

const store = useStewardSuggestionsStore()
const auth = useAuthStore()
let contextEpoch = 0

const submitting = ref(false)
const dismissing = ref(false)
const resultNote = ref<string | null>(null)
const errorNote = ref<string | null>(null)

const KIND_LABELS = SUGGESTION_KIND_LABELS

const STATE_LABELS = SUGGESTION_STATE_LABELS

const pairText = computed(() => {
  const s = suggestion.value
  if (s === null) return ''
  const subject = s.subject_name ?? `用户 ${s.subject_user_id}`
  const object = s.object_name ?? (s.object_user_id !== null ? `用户 ${s.object_user_id}` : '—')
  return `${subject} · ${object}`
})

// A-R1：方向/称谓语义由服务端 presentation 承载；前端绝不从 fact_type
// 构造展示（旧载荷安全降级为中性文案，不回退 raw enum 映射）。
const relationText = computed(() => {
  const s = suggestion.value
  if (s === null) return null
  if (s.presentation !== null) return s.presentation.summary
  if (s.kind === 'term_preference') {
    return typeof s.value.term === 'string' ? s.value.term : null
  }
  return null
})

const evidenceText = computed(() => {
  const s = suggestion.value
  if (s === null) return null
  if (s.presentation !== null) {
    const kind = s.presentation.evidence.kind
    if (kind === 'confirmed_path') {
      return `依据：${s.presentation.evidence.related_fact_count ?? 0} 条可核验的相关已确认事实。`
    }
    if (kind === 'inferred_path') {
      return `依据：确定性推测路径（${s.presentation.evidence.related_fact_count ?? 0} 条相关事实），待核实。`
    }
    if (kind === 'unverified_candidate') {
      return '依据：模型线索，暂无可核验的相关事实，待核实。'
    }
    return '依据：当前不可用。'
  }
  return '依据：暂无可核验的相关事实，待核实。'
})

const canSubmit = computed(
  () => suggestion.value !== null && suggestion.value.allowed_actions.includes('submit'),
)
const canDismiss = computed(
  () => suggestion.value !== null && suggestion.value.allowed_actions.includes('dismiss'),
)
const isRelation = computed(() => suggestion.value?.kind === 'relation_proposal')
const isPreference = computed(() => suggestion.value?.kind === 'term_preference')

const proposalNote = computed(() => {
  const linked = suggestion.value?.linked_proposal
  if (!linked) return null
  const labels: Record<string, string> = {
    proposed: '待确认',
    confirmed: '已确认',
    rejected: '已拒绝',
    disputed: '存疑',
    revoked: '已撤销',
  }
  const state = labels[linked.state] ?? '待核实'
  const pending = suggestion.value?.pending_confirmations?.length ?? 0
  const confirmationNote = linked.state === 'proposed'
    ? pending > 0
      ? `当前有 ${pending} 位成员可确认该提案。`
      : '当前没有可用的确认人，关系尚未确认。'
    : ''
  return `关联提案状态：${state}。${confirmationNote}`
})

watch(
  () => [opened.value, props.spaceId, auth.user?.id, suggestion.value?.id] as const,
  () => {
    contextEpoch += 1
    submitting.value = false
    dismissing.value = false
    resultNote.value = null
    errorNote.value = null
  },
  { flush: 'sync' },
)

onBeforeUnmount(() => { contextEpoch += 1 })

function setSuggestion(value: SuggestionItem | null): void {
  suggestionModel.value = value
}

defineExpose({ setSuggestion })

async function onSubmit(): Promise<void> {
  const s = suggestion.value
  if (s === null || !canSubmit.value || submitting.value || dismissing.value) return
  const requestEpoch = contextEpoch
  const spaceId = props.spaceId
  submitting.value = true
  resultNote.value = null
  errorNote.value = null
  try {
    const result = await store.submit(
      spaceId,
      s.id,
      { expected_revision: s.revision, evidence_hash: s.evidence_hash, confirm: true },
      // 每次点击生成新幂等键：重试同一语义由调用方持有同一键
      crypto.randomUUID(),
    )
    if (requestEpoch !== contextEpoch || result === null) return
    suggestionModel.value = 'linked_proposal' in result
      ? {
        ...result.suggestion,
        linked_proposal: result.linked_proposal,
        pending_confirmations: result.pending_confirmations,
      }
      : result.suggestion
    resultNote.value = 'linked_proposal' in result ? '已提交关系提案。' : '已按你的选择完成更新。'
  } catch {
    if (requestEpoch === contextEpoch) {
      errorNote.value = '提交未成功：建议可能已被处理或证据已变化，请稍后刷新。'
    }
  } finally {
    if (requestEpoch === contextEpoch) submitting.value = false
  }
}

async function onDismiss(): Promise<void> {
  const s = suggestion.value
  if (s === null || !canDismiss.value || dismissing.value || submitting.value) return
  const requestEpoch = contextEpoch
  const spaceId = props.spaceId
  dismissing.value = true
  errorNote.value = null
  try {
    const detail = await store.dismiss(spaceId, s.id, s.revision)
    if (requestEpoch !== contextEpoch) return
    suggestionModel.value = detail
    resultNote.value = '已忽略：同一证据版本不会再次向你提醒。'
  } catch {
    if (requestEpoch === contextEpoch) errorNote.value = '忽略未成功，请稍后重试。'
  } finally {
    if (requestEpoch === contextEpoch) dismissing.value = false
  }
}
</script>

<template>
  <NModal
    v-model:show="opened"
    preset="card"
    class="suggestion-dialog"
    title="建议详情"
    data-test="suggestion-dialog"
  >
    <NSpin v-if="suggestion === null && resultNote === null" :show="true" />
    <div v-if="suggestion !== null" class="suggestion-body">
      <p class="sug-kind">{{ KIND_LABELS[suggestion.kind] }}</p>
      <p class="sug-kind" data-test="suggestion-state">
        {{ isPreference && suggestion.state === 'proposed' ? '无需处理' : STATE_LABELS[suggestion.state] }}
      </p>
      <h3 class="sug-pair" data-test="suggestion-pair">{{ pairText }}</h3>
      <p v-if="relationText !== null" class="sug-relation" data-test="suggestion-relation">
        {{ relationText }}
      </p>
      <p class="sug-evidence" data-test="suggestion-evidence">{{ evidenceText }}</p>
      <p v-if="isRelation && canSubmit" class="sug-privacy">
        该建议只是线索：提交后由有权确认者按现有流程处理。
      </p>
      <p v-else-if="isPreference" class="sug-privacy">
        称谓由管家自动维护，无需逐条处理；保留叫法只更新你的个人偏好，并在各空间生效。
      </p>
      <p v-if="proposalNote" class="sug-note" data-test="suggestion-proposal-state">
        {{ proposalNote }}
      </p>

      <div class="sug-actions">
        <NButton
          v-if="canSubmit"
          size="small"
          type="primary"
          :loading="submitting"
          :disabled="dismissing"
          data-test="suggestion-submit"
          @click="onSubmit"
        >
          {{ isPreference ? '保留为我的叫法' : '提交关系提案' }}
        </NButton>
        <NButton
          v-if="canDismiss"
          size="small"
          secondary
          :loading="dismissing"
          :disabled="submitting"
          data-test="suggestion-dismiss"
          @click="onDismiss"
        >
          忽略
        </NButton>
        <NButton size="small" quaternary @click="opened = false">关闭</NButton>
      </div>
      <p v-if="isRelation && canSubmit" class="sug-hint">
        确认资格由现有授权流程决定；空间管理员身份本身不提供代确认权限。
      </p>
    </div>
    <p v-if="resultNote !== null" class="sug-note sug-note--ok" data-test="suggestion-result">
      {{ resultNote }}
    </p>
    <p v-else-if="errorNote !== null" class="sug-note sug-note--err" data-test="suggestion-error">
      {{ errorNote }}
    </p>
  </NModal>
</template>

<style scoped>
.suggestion-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sug-kind {
  margin: 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.sug-pair {
  margin: 0;
  font-size: 17px;
  color: var(--fg-ink);
}

.sug-relation,
.sug-evidence,
.sug-privacy,
.sug-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--fg-ink-secondary);
}

.sug-note {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.7;
}

.sug-note--ok { color: var(--fg-success, #18a058); }
.sug-note--err { color: var(--fg-danger, #d03050); }

.sug-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}

@media (max-width: 600px) {
  .sug-actions :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度（非 scoped 必需）。
 * naive 的 preset="card" 没有内置宽度上限（card 的 width:100% 会取到视口宽），
 * 所以每个卡片弹窗都要在这里给出宽度；width: min(Npx, calc(100vw - 48px))
 * 在窄屏保留 24px 双侧留白。见 spec/frontend/component-guidelines.md。
 * 此处保持原来的 520px（原为内联 maxWidth，收敛为同形约定）。 */
[data-test='suggestion-dialog'] {
  width: min(520px, calc(100vw - 48px));
}
</style>
