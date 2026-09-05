<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NPopover, NRadioButton, NRadioGroup, NSpin } from 'naive-ui'

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
    labelStyle: { fill: 'var(--fg-ink-secondary)', fontSize: '12px' },
    labelBgStyle: { fill: 'var(--fg-surface-raised)' },
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
  void router.push({ name: 'person-profile', params: { userId: String(userId) } })
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
    padding: 0.1,
  })
}

function focusSelf(): void {
  if (viewerId.value === null) return
  const selfNode = positionedNodes.value.find((node) => node.isSelf)
  if (!selfNode) return
  void setCenter(selfNode.x + 75, selfNode.y + 60, { zoom: 1.1 })
}

async function reload(): Promise<void> {
  await loadView(true)
}

/** 返回家庭卡（PRD §2.5 底部操作）：切换到最近/第一个可用家庭空间；无可用空间时不动作 */
async function backToHousehold(): Promise<void> {
  const target = householdSpaces.value[0]
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
      <h1 class="view-title">家族树</h1>
      <span v-if="spaceName" class="space-name" data-test="lineage-space-name">{{ spaceName }}</span>
    </header>

    <!-- 当前空间不是 lineage：安全上下文提示，不渲染任何投影内容 -->
    <section v-if="!isLineageContext" class="context-panel" data-test="lineage-context-panel">
      <NAlert type="info" :show-icon="true" data-test="not-lineage-hint">
        家族树按家族空间展示当前授权投影。请先选择一个家族空间。
      </NAlert>
      <div v-if="lineageSpaces.length > 0" class="context-switch">
        <NButton
          v-for="space in lineageSpaces"
          :key="space.id"
          size="small"
          secondary
          :data-test="`switch-to-lineage-${space.id}`"
          @click="spaceContext.switchSpace(space.id)"
        >
          进入「{{ space.name }}」
        </NButton>
      </div>
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
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M8 3H5a2 2 0 0 0-2 2v3" />
              <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
              <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
              <path d="M3 16v3a2 2 0 0 0 2 2h3" />
            </svg>
          </button>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="focus-self"
            aria-label="回到自己"
            title="回到自己"
            @click="focusSelf"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="7" />
              <circle cx="12" cy="12" r="2.5" />
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
            </svg>
          </button>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="reload-view"
            aria-label="重新加载"
            title="重新加载"
            @click="reload"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
            </svg>
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
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <path d="M8 6h13M8 12h13M8 18h13" />
                  <path d="M3 6h.01M3 12h.01M3 18h.01" />
                </svg>
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
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="m3 10 9-7 9 7v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <path d="M9 21v-8h6v8" />
            </svg>
          </button>
        </div>

        <!-- 画布：静态星空/点阵由壳背景提供，这里只画结构 -->
        <section class="canvas-section" data-test="canvas-section">
          <div class="canvas-wrap">
            <VueFlow
              :nodes="flowNodes"
              :edges="flowEdges"
              fit-view-on-init
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
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 24px 20px 28px;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  animation: fadeIn 0.5s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.view-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding-bottom: 8px;
  border-bottom: 2px solid color-mix(in srgb, var(--fg-accent) 15%, transparent);
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--fg-accent) 6%, transparent) 0%,
    transparent 60%
  );
  margin: -8px -8px 0 -8px;
  padding: 8px 8px 12px 8px;
  border-radius: var(--fg-radius-control);
}

.view-title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.02em;
  background: linear-gradient(135deg, var(--fg-accent) 0%, color-mix(in srgb, var(--fg-accent) 70%, var(--fg-ink) 30%) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: slideInLeft 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes slideInLeft {
  from {
    opacity: 0;
    transform: translateX(-16px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.space-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--fg-ink-secondary);
  padding: 2px 12px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 10%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 6%, transparent) 100%
  );
  border: 1px solid color-mix(in srgb, var(--fg-accent) 20%, transparent);
  border-radius: 999px;
  animation: fadeInRight 0.6s ease;
  animation-delay: 0.2s;
  animation-fill-mode: backwards;
}

@keyframes fadeInRight {
  from {
    opacity: 0;
    transform: translateX(12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.context-panel,
.status-panel {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 16px;
  padding: 24px;
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 97%, var(--fg-accent) 3%) 100%
  );
  border: 1px solid color-mix(in srgb, var(--fg-line-strong) 80%, transparent);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 8%, transparent),
    var(--fg-shadow-card);
  max-width: 600px;
  animation: scaleIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes scaleIn {
  from {
    opacity: 0;
    transform: scale(0.95);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.context-switch {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.status-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  font-family: var(--fg-font-display);
  color: var(--fg-ink);
  background: linear-gradient(135deg, var(--fg-accent) 0%, var(--fg-ink) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.status-text {
  margin: 0;
  font-size: 14px;
  color: var(--fg-ink-secondary);
  line-height: 1.7;
}

.loading-spin {
  min-height: 160px;
}

/* 工具栏：底部居中悬浮胶囊毛玻璃 + 现代操作组 */
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 20px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: 999px;
  box-shadow:
    0 8px 32px 0 color-mix(in srgb, var(--fg-ink) 10%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 12%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 30%, transparent);
  animation: slideDown 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
  width: fit-content;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateY(-12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 降级方案：不支持 backdrop-filter 时使用不透明背景 */
@supports not (backdrop-filter: blur(12px)) {
  .toolbar {
    background: var(--fg-surface-raised);
  }
}

.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
  color: var(--fg-ink);
}

.legend li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--fg-radius-control);
  transition: all 0.3s ease;
}

.legend li:hover {
  background: color-mix(in srgb, var(--fg-accent) 6%, transparent);
  transform: translateX(4px);
}

/* 画布容器：现代化边框和深度感 */
.canvas-section {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 460px;
  animation: fadeInUp 0.6s ease;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.canvas-wrap {
  position: relative;
  flex: 1;
  min-height: 480px;
  background-color: var(--fg-surface);
  background-image:
    radial-gradient(ellipse 90% 70% at 50% -10%, color-mix(in srgb, var(--fg-accent) 12%, transparent), transparent 70%),
    radial-gradient(ellipse 60% 50% at 85% 95%, color-mix(in srgb, var(--fg-info) 10%, transparent), transparent 60%),
    radial-gradient(circle 1.8px at 28px 36px, var(--fg-dot) 100%, transparent),
    radial-gradient(circle 1.2px at 160px 140px, color-mix(in srgb, var(--fg-dot) 75%, transparent) 100%, transparent),
    radial-gradient(circle 1.5px at 290px 80px, var(--fg-dot) 100%, transparent);
  background-size:
    100% 100%,
    100% 100%,
    260px 260px,
    190px 190px,
    340px 340px;
  border: 1.5px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.8);
  overflow: hidden;
  box-shadow:
    inset 0 2px 16px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    0 8px 32px 0 color-mix(in srgb, var(--fg-ink) 8%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 10%, transparent);
}

/* 中心聚焦效果：径向渐变遮罩，中心清晰，边缘模糊 */
.canvas-wrap::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    ellipse 60% 50% at 50% 50%,
    transparent 0%,
    transparent 40%,
    color-mix(in srgb, var(--fg-surface) 15%, transparent) 70%,
    color-mix(in srgb, var(--fg-surface) 35%, transparent) 90%,
    color-mix(in srgb, var(--fg-surface) 45%, transparent) 100%
  );
  z-index: 1;
  transition: opacity 0.5s ease;
}

/* 悬停画布时减弱聚焦效果，让用户看清周围 */
.canvas-wrap:hover::after {
  opacity: 0.5;
}

/* 确保画布内容在遮罩层下方 */
.canvas-wrap :deep(.vue-flow) {
  position: relative;
  z-index: 0;
}

/* 边样式：现代化连线 */
.canvas-wrap :deep(.fg-view-edge .vue-flow__edge-path) {
  stroke: var(--fg-ink-secondary);
  stroke-width: 2;
  transition: all 0.3s ease;
  filter: drop-shadow(0 1px 2px color-mix(in srgb, var(--fg-ink) 10%, transparent));
}

.canvas-wrap :deep(.fg-view-edge:hover .vue-flow__edge-path) {
  stroke: var(--fg-accent);
  stroke-width: 2.5;
  filter: drop-shadow(0 2px 4px color-mix(in srgb, var(--fg-accent) 30%, transparent));
}

/* Controls 现代化样式 */
.canvas-wrap :deep(.vue-flow__controls) {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-surface-raised) 90%, transparent) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 95%, transparent) 100%
  );
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid color-mix(in srgb, var(--fg-line-strong) 60%, transparent);
  border-radius: calc(var(--fg-radius-control) * 1.5);
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 8%, transparent),
    0 4px 12px color-mix(in srgb, var(--fg-ink) 10%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 100%, transparent);
}

/* 降级方案：不支持 backdrop-filter 时使用不透明背景 */
@supports not (backdrop-filter: blur(12px)) {
  .canvas-wrap :deep(.vue-flow__controls) {
    background: var(--fg-surface-raised);
  }
}

.canvas-wrap :deep(.vue-flow__controls-button) {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: none;
  border-bottom: 1px solid color-mix(in srgb, var(--fg-line) 50%, transparent);
  background-color: transparent;
  color: var(--fg-ink-secondary);
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.canvas-wrap :deep(.vue-flow__controls-button:last-child) {
  border-bottom: none;
}

.canvas-wrap :deep(.vue-flow__controls-button:hover) {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 12%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 8%, transparent) 100%
  );
  color: var(--fg-accent);
  transform: scale(1.08);
}

.canvas-wrap :deep(.vue-flow__controls-button:active) {
  transform: scale(0.95);
}

.canvas-wrap :deep(.vue-flow__controls-button:disabled) {
  color: var(--fg-ink-faint);
  cursor: default;
  opacity: 0.4;
}

.canvas-wrap :deep(.vue-flow__controls-button:disabled:hover) {
  background: transparent;
  transform: none;
}

.canvas-wrap :deep(.vue-flow__controls-button svg) {
  width: 16px;
  height: 16px;
  transition: transform 0.3s ease;
}

.canvas-wrap :deep(.vue-flow__controls-button:hover svg) {
  transform: scale(1.1);
}

/* 移动端（≤768px）：工具栏收敛为紧凑单行（可横向滑动，仅工具不恢复列表布局）；
   触控画布双指缩放/拖拽平移由 VueFlow 的 zoom-on-pinch/pan-on-drag 提供，
   圆形 FAB 自带 44px 点按目标，布局切换 radio 补足 44px */
@media (max-width: 768px) {
  .family-tree-view {
    padding: 16px 12px 20px;
    gap: 12px;
  }

  .view-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .view-title {
    font-size: 22px;
  }

  .toolbar {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding: 10px 12px;
    padding-bottom: 12px;
  }

  .toolbar > * {
    flex: 0 0 auto;
  }

  .toolbar :deep(.n-button--small-type),
  .toolbar :deep(.n-radio-button) {
    min-height: 44px;
  }

  .canvas-wrap {
    min-height: 360px;
  }

  .status-panel,
  .context-panel {
    padding: 18px;
  }
}

/* 动效降级支持 */
@media (prefers-reduced-motion: reduce) {
  .family-tree-view,
  .view-title,
  .space-name,
  .status-panel,
  .context-panel,
  .toolbar,
  .canvas-section,
  .legend li {
    animation: none !important;
    transition: none !important;
  }

  .canvas-wrap :deep(.vue-flow__controls-button),
  .canvas-wrap :deep(.vue-flow__controls-button svg),
  .canvas-wrap :deep(.fg-view-edge .vue-flow__edge-path) {
    transition: none !important;
  }

  .canvas-wrap :deep(.vue-flow__controls-button:hover),
  .canvas-wrap :deep(.vue-flow__controls-button:active),
  .legend li:hover {
    transform: none;
  }
}
</style>
