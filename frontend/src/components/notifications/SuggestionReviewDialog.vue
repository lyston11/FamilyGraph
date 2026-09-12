<script setup lang="ts">
// Steward 建议详情/确认弹层（09-11 candidate-review）：
// - 展示：双方、关系/建议结构化值、证据摘要（fact 计数）、隐私影响提示；
//   只渲染服务端安全显示字段（无 raw model payload，后端不下发）；
// - 动作分离：打开弹层只读（open_details），提交（submit）需用户显式点击
//   且带 confirm=true + 每次生成的 Idempotency-Key；驳回（dismiss）独立按钮；
// - 终态/无权动作由服务端 allowed_actions 决定，前端不本地推导授权；
// - relation_proposal 提交成功只显示「提案已发起，等待当事人确认」，
//   绝不显示为关系已确认（pending_confirmations 列表原样展示）；
// - 身份重复/资料缺口：v1 无 submit，仅展示指引（转既有资料/去重流程）。
import { NButton, NModal, NSpin } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import type { SuggestionItem } from '@/types/api'

const props = defineProps<{ spaceId: number }>()
const opened = defineModel<boolean>('opened', { required: true })
const suggestionModel = defineModel<SuggestionItem | null>('suggestion', { default: null })
const suggestion = computed<SuggestionItem | null>(() => suggestionModel.value ?? null)

const store = useStewardSuggestionsStore()

const submitting = ref(false)
const dismissing = ref(false)
const resultNote = ref<string | null>(null)
const errorNote = ref<string | null>(null)

const KIND_LABELS: Record<SuggestionItem['kind'], string> = {
  relation_proposal: '关系线索',
  term_preference: '称谓偏好',
  identity_duplicate: '疑似重复档案',
  missing_information: '资料缺口',
}

const RELATION_LABELS: Record<string, string> = {
  biological_parent: '生物学亲子',
  adoptive_parent: '收养亲子',
  step_parent: '继亲',
  guardian: '监护',
  spouse: '配偶',
  partner: '伴侣',
  direct_sibling: '兄弟姐妹',
}

const pairText = computed(() => {
  const s = suggestion.value
  if (s === null) return ''
  const subject = s.subject_name ?? `用户 ${s.subject_user_id}`
  const object = s.object_name ?? (s.object_user_id !== null ? `用户 ${s.object_user_id}` : '—')
  return `${subject} · ${object}`
})

const relationText = computed(() => {
  const s = suggestion.value
  if (s === null) return null
  if (s.kind === 'relation_proposal') {
    const factType = typeof s.value.fact_type === 'string' ? s.value.fact_type : ''
    return RELATION_LABELS[factType] ?? factType
  }
  if (s.kind === 'term_preference') {
    return typeof s.value.term === 'string' ? s.value.term : null
  }
  return typeof s.value.code === 'string' ? s.value.code : null
})

const canSubmit = computed(
  () => suggestion.value !== null && suggestion.value.allowed_actions.includes('submit'),
)
const canDismiss = computed(
  () => suggestion.value !== null && suggestion.value.allowed_actions.includes('dismiss'),
)
const isRelation = computed(() => suggestion.value?.kind === 'relation_proposal')

watch(opened, (value) => {
  if (value) {
    resultNote.value = null
    errorNote.value = null
  }
})

function setSuggestion(value: SuggestionItem | null): void {
  suggestionModel.value = value
}

defineExpose({ setSuggestion })

async function onSubmit(): Promise<void> {
  const s = suggestion.value
  if (s === null || submitting.value) return
  submitting.value = true
  resultNote.value = null
  errorNote.value = null
  try {
    await store.submit(
      props.spaceId,
      s.id,
      { expected_revision: s.revision, evidence_hash: s.evidence_hash, confirm: true },
      // 每次点击生成新幂等键：重试同一语义由调用方持有同一键
      crypto.randomUUID(),
    )
    resultNote.value = isRelation.value
      ? '提案已发起：等待有权当事人完成确认，确认完成前不会进入家庭图。'
      : '已按你的选择完成更新。'
  } catch {
    errorNote.value = '提交未成功：建议可能已被处理或证据已变化，请稍后刷新。'
  } finally {
    submitting.value = false
  }
}

async function onDismiss(): Promise<void> {
  const s = suggestion.value
  if (s === null || dismissing.value) return
  dismissing.value = true
  errorNote.value = null
  try {
    await store.dismiss(props.spaceId, s.id, s.revision)
    resultNote.value = '已驳回：同一证据版本在本设备账号上不会再次提醒。'
  } catch {
    errorNote.value = '驳回未成功，请稍后重试。'
  } finally {
    dismissing.value = false
  }
}
</script>

<template>
  <NModal
    v-model:show="opened"
    preset="card"
    class="suggestion-dialog"
    :style="{ maxWidth: '520px' }"
    title="待核实详情"
    data-test="suggestion-dialog"
  >
    <NSpin v-if="suggestion === null" :show="false" />
    <div v-else class="suggestion-body">
      <p class="sug-kind">{{ KIND_LABELS[suggestion.kind] }}</p>
      <h3 class="sug-pair" data-test="suggestion-pair">{{ pairText }}</h3>
      <p v-if="relationText !== null" class="sug-relation" data-test="suggestion-relation">
        建议：{{ relationText }}
      </p>
      <p class="sug-evidence" data-test="suggestion-evidence">
        证据：基于 {{ suggestion.evidence_summary.fact_count }} 条已确认的家庭事实生成。
      </p>
      <p class="sug-privacy">
        该建议只是线索：接受后会先发起需要当事人确认的申请，不会直接修改家庭关系。
      </p>

      <p v-if="resultNote !== null" class="sug-note sug-note--ok" data-test="suggestion-result">
        {{ resultNote }}
      </p>
      <p v-else-if="errorNote !== null" class="sug-note sug-note--err" data-test="suggestion-error">
        {{ errorNote }}
      </p>

      <div class="sug-actions">
        <NButton
          v-if="canSubmit"
          size="small"
          type="primary"
          :loading="submitting"
          data-test="suggestion-submit"
          @click="onSubmit"
        >
          确认并提交
        </NButton>
        <NButton
          v-if="canDismiss"
          size="small"
          secondary
          :loading="dismissing"
          data-test="suggestion-dismiss"
          @click="onDismiss"
        >
          驳回
        </NButton>
        <NButton size="small" quaternary @click="opened = false">关闭</NButton>
      </div>
      <p v-if="isRelation && canSubmit" class="sug-hint">
        提交后：由两位当事人按既有授权流程确认，任何第三方（含空间管理员）都不能代替确认。
      </p>
    </div>
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
