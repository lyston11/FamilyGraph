<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { useAuthStore } from '@/stores/auth'
import { useKinshipStore } from '@/stores/kinship'
import { useSpacesStore } from '@/stores/spaces'
import type { KinshipResolve, TermSourceLevel } from '@/types/kinship'

/**
 * 档案抽屉「称谓」区（V2.3 KI-5）：当前生效称谓 + 来源级别徽章 +
 * 替代称谓 + 个人纠正入口 + 「我就这么叫」使用证据。
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

// ---- 来源级别徽章 ----

const SOURCE_LEVEL_LABELS: Record<TermSourceLevel, string> = {
  personal: '个人称谓',
  space: '空间叫法',
  locale: '地区叫法',
  system: '标准称谓',
  structural: '结构默认',
}

function sourceLabel(level: TermSourceLevel | null): string {
  return level ? (SOURCE_LEVEL_LABELS[level] ?? level) : ''
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
    ElMessage.success('个人称谓已更新')
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
      ElMessage.success(`「${result.term}」已成为本空间的推荐叫法`)
    } else if (!usage.created) {
      ElMessage.info('这个叫法你已经用过了')
    } else {
      ElMessage.success('已记录你的叫法；再有另一位成员使用，它将成为空间推荐叫法')
    }
  } catch (error) {
    ElMessage.error(error instanceof ApiError && error.message ? error.message : '记录失败，请稍后重试')
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
        <el-tag size="small" data-test="kinship-term-level">{{ sourceLabel(resolve.term_source_level) }}</el-tag>
      </p>

      <div v-if="altTerms.length" class="alts" data-test="kinship-alt-terms">
        <span class="muted">其他叫法：</span>
        <el-tag
          v-for="alt in altTerms"
          :key="alt"
          size="small"
          type="info"
          class="alt-tag"
          data-test="kinship-alt-term"
          @click="openCorrection(alt)"
        >
          {{ alt }}
        </el-tag>
      </div>

      <div class="actions">
        <el-button size="small" plain data-test="kinship-correct-btn" @click="openCorrection()">
          改口
        </el-button>
        <el-button
          size="small"
          type="primary"
          plain
          :loading="callingUsage"
          data-test="kinship-call-btn"
          @click="recordUsage"
        >
          我就这么叫
        </el-button>
      </div>
    </template>

    <p v-else-if="loadingResolve" class="muted" data-test="kinship-loading">称谓解析中…</p>
    <p v-else class="muted" data-test="kinship-unresolved">暂无法确定你们的关系。</p>

    <!-- 个人纠正弹层 -->
    <el-dialog
      v-model="correctionVisible"
      title="修改我对 TA 的叫法"
      width="360px"
      append-to-body
      data-test="kinship-correction-dialog"
    >
      <p class="muted">只改变你这边的显示称谓，不会改动任何档案里的关系事实。</p>
      <el-input
        v-model="correctionTerm"
        :maxlength="MAX_TERM_LENGTH"
        show-word-limit
        placeholder="输入你的叫法"
        aria-label="新的叫法"
        data-test="kinship-correction-input"
        @keyup.enter="saveCorrection"
      />
      <p v-if="correctionError" class="error" data-test="kinship-correction-error">{{ correctionError }}</p>
      <template #footer>
        <el-button data-test="kinship-correction-cancel" @click="correctionVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="savingCorrection"
          :disabled="!correctionTerm.trim()"
          data-test="kinship-correction-save"
          @click="saveCorrection"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.kinship {
  margin-bottom: 20px;
}

.block-title {
  margin: 0 0 6px;
  font-size: 14px;
}

.term-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
}

.current-term {
  font-size: 16px;
  font-weight: 600;
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

.actions {
  display: flex;
  gap: 8px;
}

.muted {
  margin: 0 0 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.error {
  margin: 8px 0 0;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
