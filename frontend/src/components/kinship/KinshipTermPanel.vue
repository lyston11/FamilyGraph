<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useKinshipStore } from '@/stores/kinship'
import { useSpacesStore } from '@/stores/spaces'
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
const message = useMessage()

const resolve = computed(() => {
  const spaceId = spaces.currentSpaceId
  const viewerId = auth.user?.id
  if (spaceId === null || viewerId === undefined) return null
  return kinship.cachedResolve(spaceId, viewerId, props.memberId)
})
/** 首次进入尚无缓存时也渲染骨架文案，避免整区闪烁 */
const loadingResolve = ref(false)

async function refresh(force: boolean): Promise<void> {
  const spaceId = spaces.currentSpaceId
  const viewerId = auth.user?.id
  if (spaceId === null || viewerId === undefined) return
  loadingResolve.value = true
  try {
    await kinship.resolvePair(spaceId, viewerId, props.memberId, { force })
  } catch {
    // 非降级错误静默：展示「暂无法确定」而非报错打断抽屉
  } finally {
    loadingResolve.value = false
  }
}

watch(
  () => [spaces.currentSpaceId, props.memberId] as const,
  () => void refresh(true),
  { immediate: true },
)

// ---- 四级来源徽章（design.md §3.4：来源必须可见）----

const SOURCE_LEVEL_LABELS: Record<TermSourceLevel, string> = {
  personal: '个人称谓',
  space: '空间叫法',
  locale: '地区叫法',
  system: '标准称谓',
  structural: '结构默认',
}

/** 来源层级越靠前越"贴身"：personal 实底主色 / space 确认色 / locale 提案色 /
 * system、structural 中性描边（全部复用 .fg-badge--* 全站工具类） */
const SOURCE_LEVEL_CLASSES: Partial<Record<TermSourceLevel, string>> = {
  personal: 'fg-badge--accent',
  space: 'fg-badge--confirmed',
  locale: 'fg-badge--proposed',
}

function sourceLabel(level: TermSourceLevel | null): string {
  return level ? (SOURCE_LEVEL_LABELS[level] ?? level) : ''
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
  const spaceId = spaces.currentSpaceId
  const conceptCode = resolve.value?.concept_code
  const term = correctionTerm.value.trim()
  if (!spaceId || !conceptCode || !term) {
    correctionError.value = '请输入新的叫法'
    return
  }
  savingCorrection.value = true
  correctionError.value = ''
  try {
    await kinship.correctTerm(spaceId, conceptCode, term)
    // 本地立即刷新解析（KI-5：旧称谓不得继续展示）
    await refresh(true)
    message.success('个人称谓已更新')
    correctionVisible.value = false
  } catch (error) {
    correctionError.value =
      error instanceof ApiError && error.message ? error.message : '保存失败，请稍后重试'
  } finally {
    savingCorrection.value = false
  }
}

// ---- 我就这么叫（使用证据 → 两人可晋升空间叫法）----

const callingUsage = ref(false)

async function recordUsage(): Promise<void> {
  const spaceId = spaces.currentSpaceId
  const result = resolve.value
  if (!spaceId || !result?.found || !result.concept_code || !result.term) return
  callingUsage.value = true
  try {
    const usage = await kinship.submitUsage(spaceId, result.concept_code, result.term)
    if (!usage) return
    if (usage.promotion.promoted) {
      message.success(`「${result.term}」已成为本空间的推荐叫法`)
    } else if (!usage.created) {
      message.info('这个叫法你已经用过了')
    } else {
      message.success('已记录你的叫法；再有另一位成员使用，它将成为空间推荐叫法')
    }
  } catch (error) {
    message.error(error instanceof ApiError && error.message ? error.message : '记录失败，请稍后重试')
  } finally {
    callingUsage.value = false
  }
}
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
