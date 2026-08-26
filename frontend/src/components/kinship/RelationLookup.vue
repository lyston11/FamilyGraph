<script setup lang="ts">
import { computed, ref } from 'vue'

import { useKinshipStore } from '@/stores/kinship'
import { useSpacesStore } from '@/stores/spaces'
import type { TermSourceLevel } from '@/types/kinship'

/**
 * 关系查询框（V2.3 KI-3）：接受「妈妈、老妈、母亲、舅爷爷」等自由文本，
 * 调 /kinship/parse 后按 resolution_class 四级渲染。
 *
 * - 原文只读展示，任何解析产物都不覆盖用户输入（KI-4）；
 * - flag 关闭（503 KINSHIP_FLAG_DISABLED）→ 整个入口隐藏；
 * - 图上找不到证据（graph_proof.found=false）→ 降级提示，不伪造概念或方向。
 */
const MAX_LENGTH = 80

const spaces = useSpacesStore()
const kinship = useKinshipStore()

const text = ref('')

const result = computed(() => kinship.parseResult)
/** found=false：图上无可证明关系，一律降级，不渲染任何概念结论 */
const unproven = computed(() => result.value !== null && !result.value.graph_proof.found)

const canSubmit = computed(
  () =>
    spaces.currentSpaceId !== null &&
    text.value.trim().length > 0 &&
    !kinship.parseLoading,
)

async function submit(): Promise<void> {
  const spaceId = spaces.currentSpaceId
  if (!spaceId || !canSubmit.value) return
  await kinship.parseText(spaceId, text.value.trim())
}

const SOURCE_LEVEL_LABELS: Record<TermSourceLevel, string> = {
  personal: '个人称谓',
  space: '空间叫法',
  locale: '地区叫法',
  system: '标准称谓',
  structural: '结构默认',
}

function sourceLabel(level: TermSourceLevel | null | undefined): string {
  return level ? (SOURCE_LEVEL_LABELS[level] ?? level) : ''
}
</script>

<template>
  <section
    v-if="!kinship.isDisabled && spaces.currentSpace"
    class="lookup"
    data-test="relation-lookup"
  >
    <p class="block-title">关系查询</p>
    <!-- ambiguous 追问：内联展示后允许再次输入 -->
    <el-alert
      v-if="result?.resolution_class === 'ambiguous' && result.clarifying_question"
      type="info"
      :closable="false"
      class="clarify"
      data-test="lookup-clarify"
    >
      {{ result.clarifying_question }}
    </el-alert>

    <div class="input-row">
      <el-input
        v-model="text"
        :maxlength="MAX_LENGTH"
        show-word-limit
        clearable
        placeholder='试试输入叫法，如「妈妈」「舅爷爷」「奶奶的兄弟」'
        aria-label="关系查询输入"
        data-test="lookup-input"
        @keyup.enter="submit"
      />
      <el-button
        type="primary"
        plain
        :loading="kinship.parseLoading"
        :disabled="!canSubmit"
        data-test="lookup-submit"
        @click="submit"
      >
        解析
      </el-button>
    </div>

    <!-- determined：概念称谓 + 结构路径依据 + 词素 chips -->
    <div
      v-if="result?.resolution_class === 'determined' && result.candidate && !unproven"
      class="panel determined"
      data-test="lookup-determined"
    >
      <span class="term">{{ result.candidate.term }}</span>
      <el-tag size="small" type="success">{{ sourceLabel(result.candidate.term_source_level) }}</el-tag>
      <code v-if="result.candidate.concept_code" class="concept">{{ result.candidate.concept_code }}</code>
      <p v-if="result.graph_proof.explanation_structural" class="basis" data-test="lookup-path-basis">
        图上依据：{{ result.graph_proof.explanation_structural }}
      </p>
      <div v-if="result.evidence_morphemes.length" class="morphemes" data-test="lookup-morphemes">
        <span class="muted">依据词素：</span>
        <el-tag v-for="m in result.evidence_morphemes" :key="m" size="small" type="info">{{ m }}</el-tag>
      </div>
    </div>

    <!-- supported：一句话确认提案卡片；明确不会自动改动档案事实 -->
    <div
      v-else-if="result?.resolution_class === 'supported'"
      class="panel supported"
      data-test="lookup-supported"
    >
      <el-alert type="warning" :closable="false">
        <p v-for="(proposal, index) in result.proposals" :key="index" class="proposal-line">
          建议：{{ proposal.summary }}
        </p>
        <p class="muted">在你确认之前，这不会自动改动任何档案事实。</p>
      </el-alert>
    </div>

    <!-- conflicting：冲突列表 + 原文保留展示 -->
    <div
      v-else-if="result?.resolution_class === 'conflicting'"
      class="panel conflicting"
      data-test="lookup-conflicting"
    >
      <ul class="conflict-list">
        <li v-for="(conflict, index) in result.conflicts" :key="index">{{ conflict }}</li>
      </ul>
      <p class="muted" data-test="lookup-original-text">你输入的原文「{{ result.normalized_text }}」已保留。</p>
    </div>

    <!-- found=false：优雅降级，不伪造父母或方向 -->
    <p v-else-if="unproven" class="degraded" data-test="lookup-unproven">
      图上暂时找不到可证明的关系路径，不能下这个结论。
    </p>

    <p v-if="kinship.parseError" class="error" data-test="lookup-error">{{ kinship.parseError }}</p>
  </section>
</template>

<style scoped>
.lookup {
  margin-bottom: 8px;
}

.block-title {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
}

.clarify {
  margin-bottom: 8px;
}

.input-row {
  display: flex;
  gap: 8px;
  max-width: 420px;
}

.panel {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.term {
  font-size: 16px;
  font-weight: 600;
  margin-right: 8px;
}

.concept {
  margin-left: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.basis {
  margin: 8px 0 4px;
  font-size: 13px;
}

.morphemes {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.proposal-line {
  margin: 0 0 4px;
}

.conflict-list {
  margin: 0 0 6px;
  padding-left: 18px;
  color: var(--el-color-danger);
  font-size: 13px;
}

.muted {
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.degraded {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.error {
  margin: 8px 0 0;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
