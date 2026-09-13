<script setup lang="ts">
import { computed, ref } from 'vue'
import { NAlert, NButton } from 'naive-ui'

import {
  confirmInferredEdge,
  dismissInferredEdge,
} from '@/api/stewardInferred'
import type { InferredEdgeConfirmResult, PersonalFamilyViewInferredEdge } from '@/types/api'

/**
 * 推测边操作面板（09-13 推测层；FamilyTreeView 虚线边点击入口）：
 *
 * - 展示：单跳推测关系（A —kind→ B）与确定性解析称谓；新上树成员的
 *   viewer 视角推测称谓；证据事实数（无姓名/原文，安全摘要）；
 * - 操作（写操作在本面板发起，成功后由页面强制刷新投影）：
 *   确认 = 两步确认（先打开确认态再显式执行）；有权当事人 200 转正，
 *   无权 viewer 202 代为提案（绝不显示为已确认，服务端 state 原样透传）；
 *   驳回 = 两步确认（同证据不再上树）；撤销驳回不在树上渲染（rejected 边
 *   不进入 PFV payload，预留 API 供后续列表表面使用）；
 * - 面板不自持投影状态：动作结果只 emit 给页面，由页面刷新 PFV。
 */

interface Props {
  spaceId: number
  edge: PersonalFamilyViewInferredEdge
  /** 快照内 user_id → display.name 解析；解析不到返回 null（安全占位） */
  resolveName: (userId: number) => string | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'confirmed', result: InferredEdgeConfirmResult): void
  (e: 'dismissed'): void
}>()

type PendingAction = 'confirm' | 'dismiss' | null

const armedAction = ref<PendingAction>(null)
const busy = ref(false)
const errorMessage = ref('')
const outcome = ref<InferredEdgeConfirmResult | null>(null)

const subjectName = computed(() => props.resolveName(props.edge.subject_user_id) ?? '某位成员')
const objectName = computed(() => props.resolveName(props.edge.object_user_id) ?? '某位成员')

// A-R1：方向句由服务端 presentation 同源给出；旧载荷降级为中性线索，
// 不再输出 “A — kind — B” 的歧义连线文案。
const relationText = computed(
  () => props.edge.presentation?.summary ?? `${subjectName.value}与${objectName.value}之间存在待核实的推测关系`,
)

const newUserName = computed(() =>
  props.edge.new_user_id === null ? null : props.resolveName(props.edge.new_user_id) ?? '某位成员',
)

const viewerTermText = computed(() => props.edge.viewer_term ?? null)

// AC-10：只有可核验的相关路径才显示依据数量；无相关证据的候选明确“待核实”，
// 绝不把空快照说成“N 条已确认事实的确定性推断”。
const evidenceText = computed(() => {
  const kind = props.edge.presentation?.evidence.kind
  if (kind === 'confirmed_path' || kind === 'inferred_path') {
    const count = props.edge.presentation?.evidence.related_fact_count
    if (count !== null && count !== undefined && count > 0) {
      return `${count} 条可核验的相关已确认事实的确定性推断，待核实。`
    }
  }
  return '暂无可核验的相关事实，待核实。'
})

const outcomeText = computed(() => {
  const result = outcome.value
  if (result === null) return ''
  if (result.linked_proposal !== null) {
    const pendingCount = result.pending_confirmations.length
    const who = pendingCount > 0 ? `，待 ${pendingCount} 位当事人确认` : '，待有权当事人确认'
    return `已为你提交关系提案（状态：${result.linked_proposal.state}）${who}。`
  }
  return '已确认为事实，家族树将随之更新。'
})

function disarm(): void {
  armedAction.value = null
  errorMessage.value = ''
}

function arm(action: Exclude<PendingAction, null>): void {
  errorMessage.value = ''
  armedAction.value = armedAction.value === action ? null : action
}

async function runConfirm(): Promise<void> {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await confirmInferredEdge(
      props.spaceId,
      props.edge.id,
      props.edge.revision,
    )
    armedAction.value = null
    outcome.value = result
    emit('confirmed', result)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败，请稍后重试'
  } finally {
    busy.value = false
  }
}

async function runDismiss(): Promise<void> {
  busy.value = true
  errorMessage.value = ''
  try {
    await dismissInferredEdge(props.spaceId, props.edge.id, props.edge.revision)
    armedAction.value = null
    emit('dismissed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败，请稍后重试'
  } finally {
    busy.value = false
  }
}

function onConfirm(): void {
  if (armedAction.value !== 'confirm' || busy.value) return
  void runConfirm()
}

function onDismiss(): void {
  if (armedAction.value !== 'dismiss' || busy.value) return
  void runDismiss()
}
</script>

<template>
  <aside class="inferred-panel" role="complementary" aria-label="推测关系" data-test="inferred-panel">
    <header class="panel-head">
      <h2 class="panel-title">推测关系</h2>
      <NButton
        quaternary
        size="small"
        aria-label="关闭推测关系面板"
        data-test="inferred-panel-close"
        @click="emit('close')"
      >
        关闭
      </NButton>
    </header>

    <dl class="panel-body">
      <div class="field" data-test="inferred-relation">
        <dt>推测关系</dt>
        <dd>{{ relationText }}</dd>
      </div>

      <div class="field" data-test="inferred-term">
        <dt>推测称谓</dt>
        <dd>
          <span v-if="viewerTermText" data-test="inferred-viewer-term">
            {{ newUserName ?? '' }}（{{ viewerTermText }}·推测）
          </span>
          <span v-else class="degraded">暂无视角称谓</span>
        </dd>
      </div>

      <div class="field" data-test="inferred-evidence">
        <dt>依据</dt>
        <dd data-test="inferred-evidence-text">{{ evidenceText }}</dd>
      </div>
    </dl>

    <NAlert
      v-if="outcomeText"
      type="success"
      :show-icon="true"
      class="panel-alert"
      data-test="inferred-outcome"
    >
      {{ outcomeText }}
    </NAlert>
    <NAlert
      v-if="errorMessage"
      type="error"
      :show-icon="true"
      class="panel-alert"
      data-test="inferred-error"
    >
      {{ errorMessage }}
    </NAlert>

    <footer v-if="outcome === null" class="panel-actions">
      <template v-if="armedAction === 'confirm'">
        <span class="arm-hint" data-test="inferred-confirm-arm-hint">
          确认后将按现行确认合同写入事实
        </span>
        <NButton
          size="small"
          type="primary"
          :loading="busy"
          data-test="inferred-confirm-final"
          @click="onConfirm"
        >
          确认提交
        </NButton>
        <NButton size="small" quaternary :disabled="busy" @click="disarm">取消</NButton>
      </template>
      <template v-else-if="armedAction === 'dismiss'">
        <span class="arm-hint" data-test="inferred-dismiss-arm-hint">驳回后同证据不再上树</span>
        <NButton
          size="small"
          type="warning"
          :loading="busy"
          data-test="inferred-dismiss-final"
          @click="onDismiss"
        >
          确认驳回
        </NButton>
        <NButton size="small" quaternary :disabled="busy" @click="disarm">取消</NButton>
      </template>
      <template v-else>
        <NButton
          size="small"
          type="primary"
          secondary
          :disabled="busy"
          data-test="inferred-confirm-arm"
          @click="arm('confirm')"
        >
          确认为事实
        </NButton>
        <NButton
          size="small"
          secondary
          :disabled="busy"
          data-test="inferred-dismiss-arm"
          @click="arm('dismiss')"
        >
          驳回
        </NButton>
      </template>
    </footer>
  </aside>
</template>

<style scoped>
.inferred-panel {
  position: absolute; right: 18px; top: 18px; z-index: 6; width: 320px; box-sizing: border-box;
  display: flex; flex-direction: column; gap: 12px; padding: 16px;
  background: var(--fg-glass-surface-raised); border: 1px dashed var(--fg-canvas-muted);
  border-radius: 8px;
  box-shadow: 0 24px 60px color-mix(in srgb, var(--fg-canvas-surface) 55%, transparent);
}
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.panel-title { margin: 0; font-family: var(--fg-font-display); font-size: 16px; color: var(--fg-canvas-ink); }
.panel-body { display: flex; flex-direction: column; gap: 10px; margin: 0; }
.field dt { font-size: 11px; color: var(--fg-canvas-muted); margin-bottom: 2px; }
.field dd { margin: 0; font-size: 13px; color: var(--fg-canvas-ink); overflow-wrap: anywhere; }
.degraded { color: var(--fg-canvas-muted); }
.panel-alert { --n-padding: 8px 10px; }
.panel-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.arm-hint { font-size: 11px; color: var(--fg-canvas-muted); }
</style>
