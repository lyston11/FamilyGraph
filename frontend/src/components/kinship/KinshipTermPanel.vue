<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchSuggestions, restoreSuggestionTerm, submitSuggestion } from '@/api/stewardSuggestions'
import { useAuthStore } from '@/stores/auth'
import { useKinshipStore } from '@/stores/kinship'
import { useNotificationsStore } from '@/stores/notifications'
import { useSpacesStore } from '@/stores/spaces'
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import type { SuggestionItem } from '@/types/api'
import type { KinshipResolve, TermSourceLevel } from '@/types/kinship'

/**
 * 档案抽屉「称谓」区（V2.3 KI-5）：当前生效称谓 + 四级来源徽章 +
 * 路径证据（结构依据 + fact_state 摘要）+ 替代称谓 + 个人纠正入口 +
 * 「我就这么叫」使用证据。
 *
 * - from_user_id 固定当前登录者（后端同样强制，防以他人视角探测）；
 * - 个人纠正只写 personal 词条并立即刷新解析，不改结构关系；
 * - flag 关闭（503 KINSHIP_FLAG_DISABLED）→ 整区隐藏。
 */
const MAX_TERM_LENGTH = 80

const props = defineProps<{ memberId: number }>()

const auth = useAuthStore()
const spaces = useSpacesStore()
const kinship = useKinshipStore()
const notifications = useNotificationsStore()
const suggestions = useStewardSuggestionsStore()
const message = useMessage()

interface PanelContext {
  epoch: number
  viewerEpoch: number
  spaceId: number
  viewerId: number
  targetId: number
}

let contextEpoch = 0
let viewerEpoch = 0
let mounted = true

function captureContext(): PanelContext | null {
  const spaceId = spaces.currentSpaceId
  const viewerId = auth.user?.id
  if (spaceId === null || viewerId === undefined || !Number.isInteger(props.memberId) || props.memberId <= 0) return null
  return { epoch: contextEpoch, viewerEpoch, spaceId, viewerId, targetId: props.memberId }
}

function isSameViewer(context: PanelContext): boolean {
  return context.viewerEpoch === viewerEpoch && context.viewerId === auth.user?.id
}

function isCurrent(context: PanelContext): boolean {
  return mounted && context.epoch === contextEpoch && isSameViewer(context) &&
    context.spaceId === spaces.currentSpaceId && context.targetId === props.memberId
}

const resolve = computed(() => {
  const spaceId = spaces.currentSpaceId
  const viewerId = auth.user?.id
  if (spaceId === null || viewerId === undefined) return null
  return kinship.cachedResolve(spaceId, viewerId, props.memberId)
})
/** 首次进入尚无缓存时也渲染骨架文案，避免整区闪烁 */
const loadingResolve = ref(false)

async function refresh(context: PanelContext, force: boolean): Promise<void> {
  if (!isCurrent(context)) return
  loadingResolve.value = true
  try {
    await kinship.resolvePair(context.spaceId, context.viewerId, context.targetId, { force })
  } catch {
    // 非降级错误静默：展示「暂无法确定」而非报错打断抽屉
  } finally {
    if (isCurrent(context)) loadingResolve.value = false
  }
}

// ---- 四级来源徽章（design.md §3.4：来源必须可见）----

const SOURCE_LEVEL_LABELS: Record<TermSourceLevel, string> = {
  personal: '个人称谓',
  space: '空间叫法',
  locale: '地区叫法',
  system: '标准称谓',
  structural: '结构描述',
  derived: '管家称谓',
  steward: '管家称谓',
}

/** 来源层级越靠前越"贴身"：personal 实底主色 / space 确认色 / locale 提案色 /
 * system、structural 中性描边（全部复用 .fg-badge--* 全站工具类） */
const SOURCE_LEVEL_CLASSES: Partial<Record<TermSourceLevel, string>> = {
  personal: 'fg-badge--accent',
  space: 'fg-badge--confirmed',
  locale: 'fg-badge--proposed',
}

function sourceLabel(level: TermSourceLevel | null): string {
  return level ? (SOURCE_LEVEL_LABELS[level] ?? '称谓') : ''
}

function sourceBadgeClass(level: TermSourceLevel | null): string {
  return level ? (SOURCE_LEVEL_CLASSES[level] ?? 'fg-badge--neutral') : 'fg-badge--neutral'
}

// ---- 替代称谓（去重、排除主称谓）----

const altTerms = computed(() => {
  const result: KinshipResolve | null = resolve.value
  if (!result?.found) return []
  const seen = new Set([result.term])
  const terms: string[] = []
  for (const alt of result.alt_paths) {
    if (alt.term && !seen.has(alt.term)) {
      seen.add(alt.term)
      terms.push(alt.term)
    }
  }
  return terms
})

// ---- 个人纠正（弹层）----

const correctionVisible = ref(false)
const correctionTerm = ref('')
const savingCorrection = ref(false)
const correctionError = ref('')

const correctionInputProps = {
  'data-test': 'kinship-correction-input',
  'aria-label': '新的叫法',
} as VueInputHTMLAttributes

function openCorrection(prefill = ''): void {
  correctionTerm.value = prefill
  correctionError.value = ''
  correctionVisible.value = true
}

async function saveCorrection(): Promise<void> {
  const context = captureContext()
  const conceptCode = resolve.value?.concept_code
  const term = correctionTerm.value.trim()
  if (context === null || !conceptCode || !term) {
    correctionError.value = '请输入新的叫法'
    return
  }
  if (savingCorrection.value) return
  savingCorrection.value = true
  correctionError.value = ''
  try {
    const saved = await kinship.correctTerm(context.spaceId, conceptCode, term)
    if (!isSameViewer(context) || saved === null) return
    suggestions.clear()
    if (!isCurrent(context)) return
    // 本地立即刷新解析（KI-5：旧称谓不得继续展示）
    await refresh(context, true)
    if (!isCurrent(context)) return
    message.success('个人称谓已更新')
    correctionVisible.value = false
  } catch (error) {
    if (isCurrent(context)) {
      correctionError.value =
        error instanceof ApiError && error.message ? error.message : '保存失败，请稍后重试'
    }
  } finally {
    if (isCurrent(context)) savingCorrection.value = false
  }
}

// ---- 我就这么叫（使用证据 → 两人可晋升空间叫法）----

const callingUsage = ref(false)

// ---- 管家称谓建议（B-R7：可选偏好入口；不逐条待办，无需处理）----

const termSuggestions = ref<SuggestionItem[]>([])
const suggestionBusy = ref(false)

const REASON_LABELS: Record<string, string> = {
  synonym: '同义叫法',
  shorter_chain: '更简短叫法',
  preferred_usage: '你用过的叫法',
}

async function loadSuggestions(context: PanelContext): Promise<void> {
  if (!isCurrent(context)) return
  try {
    const page = await fetchSuggestions(context.spaceId, null, 50, 'term_preference', context.targetId)
    if (!isCurrent(context)) return
    termSuggestions.value = page.items.filter((item) =>
      item.space_id === context.spaceId && item.kind === 'term_preference' &&
      item.object_user_id === context.targetId && item.state === 'proposed')
  } catch {
    if (isCurrent(context)) termSuggestions.value = []
  }
}

function canKeep(item: SuggestionItem): boolean {
  return item.state === 'proposed' && item.allowed_actions.includes('submit')
}

function canRestore(item: SuggestionItem): boolean {
  return item.state === 'proposed' && item.value.can_restore === true
}

async function refreshAfterSuggestion(context: PanelContext, scope: 'personal' | 'space'): Promise<void> {
  if (!isSameViewer(context)) return
  if (scope === 'personal') suggestions.clear()
  else suggestions.clearSpace(context.spaceId)
  const refreshedView = kinship.refreshAfterTermChange(context.spaceId, scope)
  const work: Promise<unknown>[] = [refreshedView]
  if (isCurrent(context)) {
    work.push(loadSuggestions(context), refresh(context, true), notifications.refresh(context.spaceId))
  }
  await Promise.allSettled(work)
}

async function keepSuggestion(item: SuggestionItem): Promise<void> {
  const context = captureContext()
  if (context === null || suggestionBusy.value || !canKeep(item) ||
      item.space_id !== context.spaceId || item.object_user_id !== context.targetId) return
  suggestionBusy.value = true
  try {
    const result = await submitSuggestion(
      context.spaceId,
      item.id,
      { expected_revision: item.revision, evidence_hash: item.evidence_hash, confirm: true },
      crypto.randomUUID(),
    )
    if (!isSameViewer(context)) return
    if (isCurrent(context) && 'linked_preference' in result) {
      message.success(`「${result.linked_preference.term}」已保存为你的叫法（跨空间生效）`)
    }
    await refreshAfterSuggestion(context, 'personal')
  } catch {
    if (isCurrent(context)) message.error('保存未成功：建议可能已被处理，请稍后重试')
  } finally {
    if (isCurrent(context)) suggestionBusy.value = false
  }
}

async function restoreSuggestion(item: SuggestionItem): Promise<void> {
  const context = captureContext()
  if (context === null || suggestionBusy.value || !canRestore(item) ||
      item.space_id !== context.spaceId || item.object_user_id !== context.targetId) return
  const semanticHash =
    typeof item.value.semantic_hash === 'string' ? item.value.semantic_hash :
      typeof item.value.semantic_identity === 'string' ? item.value.semantic_identity : ''
  const projectionRevision =
    typeof item.value.projection_revision === 'number' ? item.value.projection_revision : 0
  if (!semanticHash || projectionRevision <= 0) return
  suggestionBusy.value = true
  try {
    await restoreSuggestionTerm(
      context.spaceId,
      item.id,
      {
        expected_revision: item.revision,
        expected_projection_revision: projectionRevision,
        semantic_hash: semanticHash,
      },
      crypto.randomUUID(),
    )
    if (!isSameViewer(context)) return
    if (isCurrent(context)) message.success('已恢复默认叫法')
    await refreshAfterSuggestion(context, 'space')
  } catch {
    if (isCurrent(context)) message.error('恢复未成功：称谓依据可能已变化，请稍后重试')
  } finally {
    if (isCurrent(context)) suggestionBusy.value = false
  }
}

async function recordUsage(): Promise<void> {
  const context = captureContext()
  const result = resolve.value
  if (context === null || callingUsage.value || !result?.found || !result.concept_code || !result.term) return
  callingUsage.value = true
  try {
    const usage = await kinship.submitUsage(context.spaceId, result.concept_code, result.term)
    if (!usage || !isCurrent(context)) return
    if (usage.promotion.promoted || usage.promotion.demoted) {
      await refresh(context, true)
      if (!isCurrent(context)) return
    }
    if (usage.promotion.promoted) {
      message.success(`「${result.term}」已成为本空间的推荐叫法`)
    } else if (!usage.created) {
      message.info('这个叫法你已经用过了')
    } else {
      message.success('已记录你的叫法；再有另一位成员使用，它将成为空间推荐叫法')
    }
  } catch (error) {
    if (isCurrent(context)) {
      message.error(error instanceof ApiError && error.message ? error.message : '记录失败，请稍后重试')
    }
  } finally {
    if (isCurrent(context)) callingUsage.value = false
  }
}

watch(() => auth.user?.id, () => { viewerEpoch += 1 }, { flush: 'sync' })

watch(
  () => [spaces.currentSpaceId, auth.user?.id, props.memberId] as const,
  () => {
    contextEpoch += 1
    termSuggestions.value = []
    suggestionBusy.value = false
    loadingResolve.value = false
    correctionVisible.value = false
    correctionTerm.value = ''
    correctionError.value = ''
    savingCorrection.value = false
    callingUsage.value = false
    const context = captureContext()
    if (context === null) return
    void refresh(context, true)
    void loadSuggestions(context)
  },
  { immediate: true, flush: 'sync' },
)

onBeforeUnmount(() => {
  mounted = false
  contextEpoch += 1
})
</script>

<template>
  <section v-if="!kinship.isDisabled" class="kinship" data-test="kinship-section">
    <h3 class="block-title">称谓</h3>

    <template v-if="resolve?.found">
      <p class="term-line">
        <span class="current-term" data-test="kinship-term">{{ resolve.term ?? '—' }}</span>
        <span class="fg-badge" :class="sourceBadgeClass(resolve.term_source_level)" data-test="kinship-term-level">
          {{ sourceLabel(resolve.term_source_level) }}
        </span>
      </p>

      <!-- 路径证据（P3-4）：结构依据 + 事实状态摘要（与主路径/替代路径同源于 resolve） -->
      <div
        v-if="resolve.explanation_structural || resolve.fact_state.confirmed || resolve.fact_state.proposed || resolve.fact_state.disputed"
        class="evidence"
        data-test="kinship-path-evidence"
      >
        <p v-if="resolve.explanation_structural" class="basis" data-test="kinship-path-basis">
          图上依据：{{ resolve.explanation_structural }}
        </p>
        <div class="fact-chips" data-test="kinship-fact-state">
          <span v-if="resolve.fact_state.confirmed > 0" class="fg-badge fg-badge--confirmed">
            已确认 {{ resolve.fact_state.confirmed }}
          </span>
          <span v-if="resolve.fact_state.proposed > 0" class="fg-badge fg-badge--proposed">
            待确认 {{ resolve.fact_state.proposed }}
          </span>
          <span v-if="resolve.fact_state.disputed > 0" class="fg-badge fg-badge--disputed">
            存疑 {{ resolve.fact_state.disputed }}
          </span>
        </div>
      </div>

      <div v-if="altTerms.length" class="alts" data-test="kinship-alt-terms">
        <span class="muted">其他叫法：</span>
        <span
          v-for="alt in altTerms"
          :key="alt"
          class="fg-badge fg-badge--neutral alt-tag"
          data-test="kinship-alt-term"
          @click="openCorrection(alt)"
        >
          {{ alt }}
        </span>
      </div>

      <div class="actions">
        <NButton size="small" secondary data-test="kinship-correct-btn" @click="openCorrection()">
          改口
        </NButton>
        <NButton
          size="small"
          type="primary"
          secondary
          :loading="callingUsage"
          data-test="kinship-call-btn"
          @click="recordUsage"
        >
          我就这么叫
        </NButton>
      </div>

      <!-- 管家称谓建议（可选偏好；关闭面板/忽略不等于拒绝） -->
      <div v-if="termSuggestions.length > 0" class="suggestions" data-test="kinship-suggestions">
        <p class="suggestions-title">管家建议</p>
        <div
          v-for="item in termSuggestions"
          :key="item.id"
          class="suggestion"
          data-test="kinship-suggestion"
        >
          <p class="suggestion-term">
            「{{ String(item.value.term ?? '') }}」
            <span class="fg-badge fg-badge--muted">
              {{ REASON_LABELS[String(item.value.reason_code ?? '')] ?? '可选叫法' }}
            </span>
          </p>
          <div class="suggestion-actions">
            <NButton
              v-if="canKeep(item)"
              size="tiny"
              type="primary"
              secondary
              :loading="suggestionBusy"
              data-test="kinship-keep-btn"
              @click="keepSuggestion(item)"
            >
              保留为我的叫法
            </NButton>
            <NButton
              v-if="canRestore(item)"
              size="tiny"
              quaternary
              :loading="suggestionBusy"
              data-test="kinship-restore-btn"
              @click="restoreSuggestion(item)"
            >
              恢复默认叫法
            </NButton>
          </div>
        </div>
      </div>
    </template>

    <p v-else-if="loadingResolve" class="muted" data-test="kinship-loading">称谓解析中…</p>
    <p v-else class="muted" data-test="kinship-unresolved">暂无法确定你们的关系。</p>

    <!-- 个人纠正弹层 -->
    <NModal
      v-model:show="correctionVisible"
      preset="card"
      title="修改我对 TA 的叫法"
      data-test="kinship-correction-dialog"
    >
      <p class="muted">只改变你这边的显示称谓，不会改动任何档案里的关系事实。</p>
      <NInput
        v-model:value="correctionTerm"
        :maxlength="MAX_TERM_LENGTH"
        show-count
        placeholder="输入你的叫法"
        :input-props="correctionInputProps"
        @keyup.enter="saveCorrection"
      />
      <p v-if="correctionError" class="error" data-test="kinship-correction-error">{{ correctionError }}</p>
      <template #footer>
        <div class="modal-actions">
          <NButton data-test="kinship-correction-cancel" @click="correctionVisible = false">取消</NButton>
          <NButton
            type="primary"
            :loading="savingCorrection"
            :disabled="!correctionTerm.trim()"
            data-test="kinship-correction-save"
            @click="saveCorrection"
          >
            保存
          </NButton>
        </div>
      </template>
    </NModal>
  </section>
</template>

<style scoped>
.kinship {
  margin-bottom: 20px;
}

.block-title {
  margin: 0 0 6px;
  font-size: 14px;
  color: var(--fg-ink);
}

.term-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
}

.current-term {
  font-family: var(--fg-font-display);
  font-size: 16px;
  font-weight: 700;
  color: var(--fg-ink);
}

.evidence {
  margin: 0 0 10px;
  padding: 8px 10px;
  background-color: var(--fg-surface-sunken);
  border-radius: var(--fg-radius-control);
}

.basis {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.fact-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.alts {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.alt-tag {
  cursor: pointer;
}

.alt-tag:hover {
  border-color: var(--fg-accent);
  color: var(--fg-accent);
}

.suggestions {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--fg-border, #e5e5e5);
}

.suggestions-title {
  font-size: 12px;
  color: var(--fg-text-secondary, #666);
  margin: 0 0 4px;
}

.suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
}

.suggestion-term {
  margin: 0;
  font-size: 13px;
}

.suggestion-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.actions {
  display: flex;
  gap: 8px;
}

.muted {
  margin: 0 0 8px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.error {
  margin: 8px 0 0;
  color: var(--fg-status-disputed);
  font-size: 13px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='kinship-correction-dialog'] {
  width: min(360px, calc(100vw - 48px));
}
</style>
