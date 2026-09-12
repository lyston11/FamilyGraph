<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NPopover, NRadioButton, NRadioGroup, NSpin } from 'naive-ui'
import { House, List, LocateFixed, Maximize, Network, RefreshCw } from 'lucide-vue-next'

import { Controls } from '@vue-flow/controls'
// 仅引入 Vue Flow 结构样式（定位/层叠）；theme-default 的写死配色不引入，
// 节点/连线/Controls 观感全部由 --fg-* token 自绘（design.md §7）
import { VueFlow, useVueFlow } from '@vue-flow/core'
import type { Edge as FlowEdge, EdgeMouseEvent, Node as FlowNode } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'

import MemberNode from '@/components/canvas/MemberNode.vue'
import RelationshipDetailPanel from '@/components/canvas/RelationshipDetailPanel.vue'
import { useSpaceContext } from '@/composables/useSpaceContext'
import {
  applyFreeCanvasLayout,
  applyTreeViewLayout,
  buildFamilyCanvas,
  type FamilyCanvasEdge,
} from '@/composables/useFamilyTreeCanvas'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type { PersonalFamilyViewData } from '@/types/api'

/**
 * 家族空间树 / FamilyTreeView（09-01 design.md §5.2）。
 *
 * 数据边界（红线）：
 * - 数据源只用 personalFamilyView store（按当前 lineage space_id load/refresh，
 *   304 复用同一安全快照）；禁止请求或读取旧 graph store 的 /api/graph/me，
 *   也不把旧 graph 位置数据混入画布；
 * - Vue Flow 数据构造在页面级 view-model（composables/useFamilyTreeCanvas.ts），
 *   MemberNode 纯展示、不发请求、不读路由；
 * - 工具只保留：树状（默认）/自由画布切换、缩放（Controls）、适应画布、回到自己、
 *   重新加载、图例；列表布局入口已删除；
 * - 状态机：never_computed/queued/running 且无数据 → 状态面板；current → 画布；
 *   stale/failed → 最近安全投影 + 清晰标注；403/404/网络错误 → 安全失败状态
 *   （不显示空间名/ID 细节）；truncated → 截断提示与已加载数量。
 */
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const pfv = usePersonalFamilyViewStore()
const spaceContext = useSpaceContext()

const { fitView, setCenter } = useVueFlow()

const viewMode = ref<'tree' | 'canvas'>('tree')
const selectedEdge = ref<FamilyCanvasEdge | null>(null)

const spaceId = computed(() => spaces.currentSpaceId)
const isLineageContext = computed(() => spaces.currentSpace?.kind === 'lineage')
const data = computed<PersonalFamilyViewData | null>(() =>
  spaceId.value === null ? null : pfv.forSpace(spaceId.value),
)
const loading = computed(() => spaceId.value !== null && pfv.isLoading(spaceId.value))
const loadError = computed(() => (spaceId.value === null ? null : pfv.errorFor(spaceId.value)))
const viewerId = computed(() => auth.user?.id ?? null)

/** 失败/错误状态不显示空间名（design.md §8：不泄露目标细节） */
const spaceName = computed(() => (loadError.value !== null ? null : spaces.currentSpace?.name ?? null))

const hasRenderableNodes = computed(() => (data.value?.nodes.length ?? 0) > 0)
const pendingComputation = computed(() => {
  const d = data.value
  return (
    d !== null &&
    d.nodes.length === 0 &&
    (d.status === 'never_computed' || d.status === 'queued' || d.status === 'running')
  )
})
const failedWithoutSnapshot = computed(
  () => data.value !== null && data.value.status === 'failed' && data.value.nodes.length === 0,
)
const emptyProjection = computed(
  () => data.value !== null && data.value.status === 'current' && data.value.nodes.length === 0,
)

/** stale/failed/计算中：用最近安全投影渲染画布，但清晰标注版本/时间/原因 */
const statusBanner = computed<{ kind: 'stale' | 'failed' | 'running'; text: string } | null>(() => {
  const d = data.value
  if (d === null || d.nodes.length === 0) return null
  const meta = `投影版本 v${d.view_version}，更新于 ${d.computed_at ?? '未知时间'}`
  if (d.status === 'stale') {
    return {
      kind: 'stale',
      text: `关系数据已过期，以下为最近一次安全投影（${meta}${d.stale_reason ? `；原因：${d.stale_reason}` : ''}）。`,
    }
  }
  if (d.status === 'failed') {
    return { kind: 'failed', text: `最近一次计算失败，以下为最近一次安全投影（${meta}）。` }
  }
  if (d.status === 'queued' || d.status === 'running') {
    return { kind: 'running', text: '关系投影正在重新计算，以下为最近一次安全投影。' }
  }
  return null
})

async function loadView(force = false): Promise<void> {
  if (spaceId.value === null || !isLineageContext.value) return
  if (force) await pfv.refresh(spaceId.value).catch(() => undefined)
  else await pfv.load(spaceId.value).catch(() => undefined)
}

onMounted(() => {
  // 家族树不替用户更换空间；当前仍是 household 时保留上下文，等待用户
  // 在壳层的「当前家族空间」选择器中明确选择目标 lineage。
  void loadView()
})

watch([spaceId, isLineageContext], () => {
  // 切换空间：清面板选择并按新上下文读取投影（旧请求由 store epoch 丢弃）
  selectedEdge.value = null
  void loadView()
})

// ---- 画布 view-model：nodes/edges 只来自已解码快照 ----

const canvasModel = computed(() =>
  data.value
    ? buildFamilyCanvas(data.value, viewerId.value)
    : { nodes: [], edges: [] as FamilyCanvasEdge[] },
)

const positionedNodes = computed(() =>
  viewMode.value === 'canvas'
    ? applyFreeCanvasLayout(canvasModel.value)
    : applyTreeViewLayout(canvasModel.value, viewerId.value),
)

const flowNodes = computed<FlowNode[]>(() =>
  positionedNodes.value.map((node) => ({
    id: `n-${node.userId}`,
    type: 'member',
    position: { x: node.x, y: node.y },
    data: {
      display: node.display,
      visibilityLevel: node.visibilityLevel,
      isSelf: node.isSelf,
      term: node.term,
    },
    draggable: viewMode.value === 'canvas',
  })),
)

const flowEdges = computed<FlowEdge[]>(() =>
  canvasModel.value.edges.map((spec) => ({
    id: spec.key,
    source: `n-${spec.sourceUserId}`,
    target: `n-${spec.targetUserId}`,
    label: spec.label ?? undefined,
    class: 'fg-view-edge',
    labelStyle: { fill: 'var(--fg-canvas-ink)', fontSize: '11px' },
    labelBgStyle: { fill: 'var(--fg-canvas-surface-raised)' },
    labelBgPadding: [6, 2] as [number, number],
    labelBgBorderRadius: 4,
  })),
)

// ---- 交互：节点/边点击 → 页面路由或只读面板；画布组件不参与路由 ----

function onNodeSelect(userId: number): void {
  if (userId === viewerId.value) {
    // 点击自己 → 家庭卡（design.md §2.3）
    void router.push({ name: 'home' })
    return
  }
  // fgBackTo：资料页返回按钮的上下文来源（PRD R1）
  void router.push({
    name: 'person-profile',
    params: { userId: String(userId) },
    state: { fgBackTo: 'family-space' },
  })
}

function openRelationshipPanel(edgeKey: string): void {
  const spec = canvasModel.value.edges.find((candidate) => candidate.key === edgeKey)
  if (spec) selectedEdge.value = spec
}

function onEdgeClick(event: EdgeMouseEvent): void {
  const edgeId = event.edge?.id
  if (typeof edgeId === 'string') openRelationshipPanel(edgeId)
}

/** 面板「申请更正/查看待办」安全跳转：只导航待办区，不产生任何写操作 */
function goNotifications(): void {
  void router.push({ name: 'notifications' })
}

// 测试与键盘入口共用：按边 key 打开只读关系说明面板
defineExpose({ openRelationshipPanel })

// ---- 工具栏动作 ----

function fitToMembers(): void {
  if (positionedNodes.value.length === 0) return
  void fitView({
    nodes: positionedNodes.value.map((node) => `n-${node.userId}`),
    padding: 0.22,
  })
}

function focusSelf(): void {
  if (viewerId.value === null) return
  const selfNode = positionedNodes.value.find((node) => node.isSelf)
  if (!selfNode) return
  void setCenter(selfNode.x + 96, selfNode.y + 72, { zoom: 1.1 })
}

async function reload(): Promise<void> {
  await loadView(true)
}

/** 返回家庭卡（PRD §2.5 底部操作）：切到本家族配对的家庭卡（显式配对优先，
 *  owner 唯一匹配回退，本人 own 的优先）；无落点时不动作 */
async function backToHousehold(): Promise<void> {
  const currentLineage = spaces.currentSpace
  const target = currentLineage ? spaces.householdForLineage(currentLineage.id) : null
  if (!target) return
  await spaceContext.switchSpace(target.id)
}

watch([positionedNodes, viewMode], () => {
  if (!hasRenderableNodes.value) return
  setTimeout(fitToMembers, 30)
})

const lineageSpaces = computed(() => spaces.spaces.filter((space) => space.kind === 'lineage'))

const householdSpaces = computed(() => spaces.spaces.filter((space) => space.kind === 'household'))

/** 快照内 user_id → 名字（面板路径显示用）；不可解析返回 null（安全占位） */
function resolveName(userId: number): string | null {
  return data.value?.nodes.find((node) => node.user_id === userId)?.display.name ?? null
}
</script>

<template>
  <main class="family-tree-view" data-test="family-tree-view">
    <header class="view-head">
      <div class="view-identity">
        <span class="view-kicker"><Network :size="15" aria-hidden="true" /> 家族空间</span>
        <h1 class="view-title">家族树</h1>
        <span v-if="spaceName" class="space-name" data-test="lineage-space-name">{{ spaceName }}</span>
      </div>
      <div v-if="data && loadError === null && isLineageContext" class="view-count">
        <strong>{{ data.nodes.length }}</strong><span>位家人</span>
      </div>
    </header>

    <!-- 当前空间不是 lineage：安全上下文提示，不渲染任何投影内容 -->
    <section v-if="!isLineageContext" class="context-panel" data-test="lineage-context-panel">
      <NAlert type="info" :show-icon="true" data-test="not-lineage-hint">
        {{ lineageSpaces.length > 0 ? '当前家庭空间还没有确定所属的家族空间，无法自动打开家族树。可在空间管理的基本设置中关联家族，或在左侧选择要查看的家族空间。' : '当前还没有可用的家族空间。' }}
      </NAlert>
    </section>

    <template v-else>
      <NSpin v-if="loading && data === null" class="loading-spin" :show="true" />

      <!-- 403/404/网络错误：安全失败状态（不显示空间名/ID 细节） -->
      <section v-else-if="loadError !== null" class="status-panel" data-test="family-tree-error">
        <h2 class="status-title">家族树暂时无法读取</h2>
        <p class="status-text">
          当前家族空间的授权投影请求失败。已按安全策略隐藏空间信息，稍后可重试。
        </p>
        <NButton size="small" data-test="family-tree-retry" @click="reload">重新加载</NButton>
      </section>

      <!-- 状态机：无数据时不画布 -->
      <section v-else-if="pendingComputation" class="status-panel" data-test="family-tree-pending">
        <h2 class="status-title">家族树尚未就绪</h2>
        <p class="status-text" data-test="pending-status-text">
          {{
            data?.status === 'never_computed'
              ? '当前家族空间的授权投影尚未计算，稍后回来查看。'
              : '当前家族空间的授权投影正在计算，以下页面稍后自动可用。'
          }}
        </p>
        <NButton size="small" data-test="pending-reload" @click="reload">重新加载</NButton>
      </section>

      <section v-else-if="failedWithoutSnapshot" class="status-panel" data-test="family-tree-failed">
        <h2 class="status-title">投影计算失败</h2>
        <p class="status-text">最近一次计算未成功，也没有可显示的安全投影。可以稍后重试。</p>
        <NButton size="small" data-test="failed-reload" @click="reload">重新加载</NButton>
      </section>

      <section v-else-if="emptyProjection" class="status-panel" data-test="family-tree-empty">
        <h2 class="status-title">暂无可见家族成员</h2>
        <p class="status-text">当前家族空间没有可展示的授权成员。成员确认后会自动出现在这里。</p>
        <NButton size="small" data-test="empty-reload" @click="reload">重新加载</NButton>
      </section>

      <template v-else-if="data !== null">
        <!-- 投影状态标注：stale/failed/计算中 -->
        <NAlert
          v-if="statusBanner"
          :type="statusBanner.kind === 'stale' ? 'warning' : statusBanner.kind === 'failed' ? 'error' : 'info'"
          :show-icon="true"
          data-test="status-banner"
        >
          {{ statusBanner.text }}
        </NAlert>

        <!-- 截断提示：服务端声明未完整返回 -->
        <NAlert v-if="data.truncated" type="warning" :show-icon="true" data-test="truncated-banner">
          当前家族规模较大，投影已截断：仅显示已加载的
          <strong data-test="truncated-count">{{ data.nodes.length }}</strong>
          位成员。
        </NAlert>

        <!-- 工具栏：树状（默认）/自由画布、适应画布、回到自己、重新加载、图例、返回家庭卡
             （操作收敛为居中悬浮胶囊 Dock 内的圆形小按钮，PRD §2.5） -->
        <div class="toolbar" data-test="canvas-toolbar">
          <NRadioGroup
            v-model:value="viewMode"
            size="small"
            name="tree-layout-mode"
            data-test="layout-switch"
            aria-label="画布布局"
          >
            <NRadioButton value="tree">树状</NRadioButton>
            <NRadioButton value="canvas">自由画布</NRadioButton>
          </NRadioGroup>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="fit-canvas"
            aria-label="适应画布"
            title="适应画布"
            @click="fitToMembers"
          >
            <Maximize :size="19" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="focus-self"
            aria-label="回到自己"
            title="回到自己"
            @click="focusSelf"
          >
            <LocateFixed :size="19" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="reload-view"
            aria-label="重新加载"
            title="重新加载"
            @click="reload"
          >
            <RefreshCw :size="19" aria-hidden="true" />
          </button>
          <NPopover trigger="click" placement="bottom-end">
            <template #trigger>
              <button
                type="button"
                class="fg-fab-btn"
                data-test="legend-trigger"
                aria-label="图例"
                title="图例"
              >
                <List :size="19" aria-hidden="true" />
              </button>
            </template>
            <ul class="legend" data-test="canvas-legend">
              <li><span class="fg-badge fg-badge--accent">我</span> 当前主体（点击回到家庭卡）</li>
              <li><span class="fg-badge fg-badge--accent">仅本人</span> self_private 字段仅本人可见</li>
              <li><span class="fg-badge fg-badge--confirmed">家庭可见</span> household_detail 家庭详情投影</li>
              <li><span class="fg-badge fg-badge--provisional">族谱摘要</span> lineage_summary 只读且不可展开</li>
              <li><span class="fg-badge fg-badge--provisional">已隐藏</span> masked 字段以锁形章表达</li>
            </ul>
          </NPopover>
          <button
            v-if="householdSpaces.length > 0"
            type="button"
            class="fg-fab-btn fg-fab-btn--accent"
            data-test="back-to-household"
            aria-label="返回家庭卡"
            title="返回家庭卡"
            @click="backToHousehold"
          >
            <House :size="19" aria-hidden="true" />
          </button>
        </div>

        <!-- 画布：静态星空/点阵由壳背景提供，这里只画结构 -->
        <section class="canvas-section" data-test="canvas-section">
          <div class="canvas-wrap">
            <VueFlow
              :nodes="flowNodes"
              :edges="flowEdges"
              fit-view-on-init
              :fit-view-params="{ padding: 0.22 }"
              :min-zoom="0.2"
              :max-zoom="1.8"
              :zoom-on-pinch="true"
              :pan-on-drag="true"
              :zoom-on-scroll="true"
              data-test="flow-canvas"
              @edge-click="onEdgeClick"
            >
              <Controls />
              <template #node-member="nodeProps">
                <MemberNode
                  :id="nodeProps.id"
                  :data="nodeProps.data"
                  @select="onNodeSelect"
                />
              </template>
            </VueFlow>

            <!-- 只读关系说明面板：覆盖层，画布位置与缩放保持不变 -->
            <RelationshipDetailPanel
              v-if="selectedEdge"
              :edge="selectedEdge.edge"
              :view-version="data.view_version"
              :computed-at="data.computed_at"
              :resolve-name="resolveName"
              @close="selectedEdge = null"
              @request-correction="goNotifications"
              @view-todos="goNotifications"
            />
          </div>
        </section>
      </template>
    </template>
  </main>
</template>

<style scoped>
.family-tree-view {
  position: relative; display: flex; flex-direction: column; gap: 14px; padding: 28px 32px 24px;
  box-sizing: border-box; height: calc(100dvh - 72px); min-height: 660px; isolation: isolate;
}
.view-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 0 8px; pointer-events: none; z-index: 2; }
.view-identity { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
.view-kicker { display: flex; align-items: center; gap: 8px; width: 100%; color: var(--fg-canvas-muted); font-size: 11px; }
.view-title { margin: 0; font-family: var(--fg-font-display); font-size: 30px; font-weight: 600; letter-spacing: 0; color: var(--fg-canvas-ink); }
.space-name { color: var(--fg-canvas-muted); font-size: 13px; overflow-wrap: anywhere; }
.view-count { display: flex; align-items: baseline; gap: 8px; flex-shrink: 0; color: var(--fg-canvas-muted); font-size: 12px; }
.view-count strong { font-size: 28px; font-weight: 500; color: var(--fg-canvas-ink); font-variant-numeric: tabular-nums; }
.context-panel, .status-panel {
  position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 16px;
  padding: 28px; max-width: 560px; margin: auto; box-sizing: border-box;
  background: var(--fg-glass-surface-raised); border: 1px solid var(--fg-glass-border); border-radius: 8px;
  box-shadow: 0 24px 60px color-mix(in srgb, var(--fg-canvas-surface) 55%, transparent);
}
.context-switch { display: flex; gap: 10px; flex-wrap: wrap; }
.status-title { margin: 0; font-size: 18px; font-family: var(--fg-font-display); color: var(--fg-ink); }
.status-text { margin: 0; font-size: 13px; color: var(--fg-ink-secondary); line-height: 1.8; }
.loading-spin { min-height: 160px; margin: auto; }
.toolbar {
  position: relative; order: 10; z-index: 5; display: flex; align-items: center; align-self: center;
  gap: 8px; flex-wrap: wrap; padding: 8px 12px; width: fit-content; max-width: 100%; box-sizing: border-box;
  background: var(--fg-glass-surface-raised); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--fg-glass-border); border-radius: 8px;
  box-shadow: 0 4px 0 color-mix(in srgb, var(--fg-canvas-surface-raised) 75%, transparent),
    0 20px 48px color-mix(in srgb, var(--fg-canvas-surface) 65%, transparent),
    inset 0 1px 0 var(--fg-surface-raised);
}
.toolbar .fg-fab-btn { box-shadow: none; border: none; background: transparent; }
.toolbar .fg-fab-btn:hover { background: var(--fg-accent-soft); }
.toolbar .fg-fab-btn--accent { background: var(--fg-accent); }
.toolbar :deep(.n-radio-group) { margin-right: 8px; }
.legend { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; font-size: 12px; color: var(--fg-ink); max-width: 290px; }
.legend li { display: flex; align-items: center; gap: 8px; }
.canvas-section { position: relative; display: flex; flex-direction: column; flex: 1; min-height: 340px; margin: 0 -32px; }
.canvas-wrap { position: relative; flex: 1; min-height: 340px; background: transparent; }
.canvas-wrap :deep(.vue-flow) { position: absolute; inset: 0; }
.canvas-wrap :deep(.fg-view-edge .vue-flow__edge-path) { stroke: var(--fg-canvas-muted); stroke-width: 1.15; opacity: 0.66; transition: stroke-width 0.2s ease, opacity 0.2s ease; }
.canvas-wrap :deep(.fg-view-edge:hover .vue-flow__edge-path),
.canvas-wrap :deep(.fg-view-edge.selected .vue-flow__edge-path) { stroke: var(--fg-canvas-ink); stroke-width: 2; opacity: 1; }
.canvas-wrap :deep(.vue-flow__edge-text) { font-family: var(--fg-font-body); }
.canvas-wrap :deep(.vue-flow__edge-textbg) { stroke: var(--fg-canvas-line); stroke-width: 0.5; }
.canvas-wrap :deep(.vue-flow__controls) {
  position: absolute; top: auto; bottom: 6px; left: 32px; z-index: 5; display: flex;
  flex-direction: column; background: color-mix(in srgb, var(--fg-canvas-surface-raised) 90%, transparent);
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--fg-canvas-line); border-radius: 8px; overflow: hidden;
  box-shadow: 0 8px 24px color-mix(in srgb, var(--fg-canvas-surface) 65%, transparent);
}
.canvas-wrap :deep(.vue-flow__controls-button) {
  display: flex; align-items: center; justify-content: center; width: 44px; height: 44px;
  padding: 0; border: none; border-bottom: 1px solid var(--fg-canvas-line);
  background: transparent; color: var(--fg-canvas-ink); cursor: pointer;
}
.canvas-wrap :deep(.vue-flow__controls-button:last-child) { border-bottom: none; }
.canvas-wrap :deep(.vue-flow__controls-button:hover) { background: var(--fg-canvas-surface-raised); }
.canvas-wrap :deep(.vue-flow__controls-button:disabled) { color: var(--fg-canvas-muted); cursor: default; opacity: 0.4; }
.canvas-wrap :deep(.vue-flow__controls-button svg) { width: 15px; height: 15px; fill: currentColor; }
@supports not (backdrop-filter: blur(12px)) {
  .toolbar { background: var(--fg-surface-raised); }
  .canvas-wrap :deep(.vue-flow__controls) { background: var(--fg-canvas-surface-raised); }
}
@media (max-width: 768px) {
  .family-tree-view { padding: 20px 16px 16px; gap: 12px; height: calc(100dvh - 182px); min-height: 580px; }
  .view-title { font-size: 24px; }
  .view-identity { gap: 6px 12px; }
  .view-count strong { font-size: 22px; }
  .view-count { gap: 4px; }
  .canvas-section { margin: 0 -16px; }
  .toolbar { flex-wrap: nowrap; align-self: stretch; width: 100%; overflow-x: auto; padding: 6px; gap: 2px; }
  .toolbar > * { flex: 0 0 auto; }
  .toolbar :deep(.n-radio-group) { margin-right: 2px; }
  .toolbar :deep(.n-radio-button) { min-height: 44px; padding: 0 9px; }
  .toolbar .fg-fab-btn { width: 40px; min-width: 40px; }
  .canvas-wrap :deep(.vue-flow__controls) { left: 16px; }
  .status-panel, .context-panel { padding: 20px; }
}
@media (prefers-reduced-motion: reduce) {
  .canvas-wrap :deep(.fg-view-edge .vue-flow__edge-path) { transition: none; }
}
</style>
