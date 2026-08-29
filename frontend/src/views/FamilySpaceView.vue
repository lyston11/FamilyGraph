<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'
import {
  NAlert,
  NButton,
  NEmpty,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NTag,
  useMessage,
} from 'naive-ui'
import type { SelectOption } from 'naive-ui'

import { Controls } from '@vue-flow/controls'
// 仅引入 Vue Flow 结构样式（定位/层叠）；theme-default 的写死配色不引入，
// 节点/连线/Controls 观感全部由 --fg-* token 自绘（design.md §3.2）
import { VueFlow, useVueFlow, type Edge as FlowEdge, type Node as FlowNode } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'

import ActionCardInbox from '@/components/actioncard/ActionCardInbox.vue'
import GlobalSearch from '@/components/common/GlobalSearch.vue'
import MemberNode from '@/components/canvas/MemberNode.vue'
import RelationLookup from '@/components/kinship/RelationLookup.vue'
import PendingProfileRefs from '@/components/member/PendingProfileRefs.vue'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import SpaceGovernanceDialog from '@/components/member/SpaceGovernanceDialog.vue'
import {
  computeCanvasLayout,
  computeGenerationLanes,
  computeTreeLayout,
  type PositionedNode,
} from '@/composables/useLayout'
import { useActionCardsStore } from '@/stores/actionCards'
import { useAuthStore } from '@/stores/auth'
import { useGraphStore } from '@/stores/graph'
import { useKinshipStore } from '@/stores/kinship'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import { getSpacePositions, putSpacePositions } from '@/api/spaces'
import type { GraphNode, Member, Relation } from '@/types/api'
import type { LayoutNodeInput } from '@/composables/useLayout'
import type { LayoutMode } from '@/types/layout'

/**
 * 家庭空间页（m1d，M1 收口）：卡片画布 + 三种布局一键切换。
 * - 画布拖拽：位置经 positions API 持久化（node_positions）
 * - 树状：computeTreeLayout，失败回退画布模式并提示；按分层结果画世代泳道底纹带（P3）
 * - 列表：长幼排序（生日升序，缺失按 id——Q1 默认方案）
 *
 * 视觉（design.md §3.2）：画布=摊开的谱卷——页面背景点阵由 token 提供
 * （不引 Vue Flow Background，防双层点阵），世代横带底纹 + 语义连线
 * （confirmed 实线 / proposed 虚线 / disputed 朱砂虚线）。
 */
const auth = useAuthStore()
const members = useMembersStore()
const spaces = useSpacesStore()
const graph = useGraphStore()
const kinship = useKinshipStore()
const actionCards = useActionCardsStore()
const router = useRouter()
const message = useMessage()

type Mode = LayoutMode
const mode = ref<Mode>((localStorage.getItem('fg.layout') as Mode) || 'canvas')
const viewScope = ref<'family' | 'clan'>('family')
const treeFailed = ref(false)
const drawerMemberId = ref<number | null>(null)
const savedPositions = ref(new Map<number, { x: number; y: number }>())
const governanceOpen = ref(false)

const { fitView } = useVueFlow()

onMounted(async () => {
  await Promise.all([
    members.load().catch(() => undefined),
    spaces.load().catch(() => undefined),
  ])
  await loadPositions()
  await graph.loadGraph(viewScope.value, 5).catch(() => undefined)
})

watch(
  () => spaces.currentSpaceId,
  async (id, previousId) => {
    // V2.3：切换空间即清空旧空间的称谓缓存与解析态（state-management.md 失效边界）
    if (typeof previousId === 'number' && typeof id === 'number' && previousId !== id) {
      kinship.resetForSpace(previousId)
      // V2.4：切换空间即清空旧空间的管家建议缓存（state-management.md 失效边界）
      actionCards.resetForSpace(previousId)
    }
    if (id !== null) {
      await loadPositions()
      await graph.loadGraph(viewScope.value, 5).catch(() => undefined)
    }
  },
)
watch(viewScope, async (scope) => {
  await graph.loadGraph(scope, 5).catch(() => undefined)
})

function memberName(id: number): string {
  return graph.nodeById.get(id)?.name ?? `#${id}`
}

/** 我的空间角色（v2 §0.2 四角色）；非 active 成员为 null */
const myRole = computed(() => {
  const mine = spaces.members.find(
    (m) => m.user_id === auth.user?.id && m.status === 'active',
  )
  return mine?.role ?? null
})

/** 发给我的 pending owner 移交（受让人视角，AC-F5） */
const incomingTransfer = computed(() =>
  spaces.transfers.find((t) => t.status === 'pending' && t.to_user === auth.user?.id) ?? null,
)

function otherSide(e: Relation): number {
  return e.from_user === (auth.user?.id ?? -1) ? e.to_user : e.from_user
}

const myEdges = computed(() =>
  graph.edges.filter(
    (e) => e.status === 'active' && (e.from_user === auth.user?.id || e.to_user === auth.user?.id),
  ),
)

async function requestJoin(userId: number) {
  try {
    await graph.requestJoin(userId)
    message.success('加入申请已发送，等待对方确认')
  } catch {
    message.error('申请发送失败，请稍后重试')
  }
}

async function loadPositions() {
  const space = spaces.currentSpace
  if (!space) return
  try {
    const rows = await getSpacePositions(space.id)
    savedPositions.value = new Map(rows.map((p) => [p.user_id, { x: p.x, y: p.y }]))
  } catch {
    savedPositions.value = new Map()
  }
}

/** 空间内可见成员 ∩ 图节点 */
const visibleMembers = computed<LayoutNodeInput[]>(() =>
  graph.nodes.map((n) => ({ id: n.id, name: n.name, gender: n.gender })),
)

/** shallowRef：布局结果整批替换、无需深层响应，避免大画布逐点代理开销 */
const layoutPositions = shallowRef<PositionedNode[]>([])
/** 空间内快速筛选（m3d）：纯前端过滤，清空即恢复完整画布 */
const canvasFilter = ref('')

watch(
  [visibleMembers, mode, canvasFilter],
  () => {
    if (mode.value === 'tree') {
      const result = computeTreeLayout(visibleMembers.value, graph.edges)
      if (!result.ok && visibleMembers.value.length > 1) {
        treeFailed.value = true
        mode.value = 'canvas'
        return
      }
      treeFailed.value = false
      layoutPositions.value = result.ok ? result.positions : []
    } else {
      layoutPositions.value = computeCanvasLayout(visibleMembers.value, savedPositions.value)
    }
    if (canvasFilter.value.trim()) {
      const q = canvasFilter.value.trim().toLowerCase()
      const keepIds = new Set(
        graph.nodes
          .filter((n) => n.name.toLowerCase().includes(q))
          .map((n) => n.id),
      )
      // 命中节点的直接邻居保留（保持上下文），其余隐藏
      for (const e of graph.edges) {
        if (e.from_user !== e.to_user) {
          if (keepIds.has(e.from_user)) keepIds.add(e.to_user)
          if (keepIds.has(e.to_user)) keepIds.add(e.from_user)
        }
      }
      layoutPositions.value = layoutPositions.value.filter((p) => keepIds.has(p.id))
    }
    setTimeout(fitToMembers, 30)
  },
  { immediate: true },
)

let saveTimer: ReturnType<typeof setTimeout> | null = null

interface DragStopEvent {
  node: { id: string; position: { x: number; y: number } }
}

function handleNodeDragStop(event: DragStopEvent) {
  const userId = Number(event.node.id.replace('n-', ''))
  const pos = event.node.position ?? layoutPositions.value.find((p) => p.id === userId)
  if (!pos) return
  savedPositions.value.set(userId, { x: pos.x, y: pos.y })
  const space = spaces.currentSpace
  if (!space || mode.value !== 'canvas') return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    const items = [...savedPositions.value.entries()].map(([uid, position]) => ({
      user_id: uid,
      ...position,
    }))
    void putSpacePositions(space.id, items).catch(() => undefined)
  }, 600)
}

const memberById = computed(() => {
  const map = new Map<number, Member>()
  for (const m of members.members) map.set(m.id, m)
  return map
})

const gnodeById = computed(() => {
  const map = new Map<number, GraphNode>()
  for (const n of graph.nodes) map.set(n.id, n)
  return map
})

/** 关系称谓投影：与节点相关的第一条带 label 边（与既有 find 语义一致） */
const labelByNode = computed(() => {
  const map = new Map<number, string>()
  for (const e of graph.edges) {
    if (e.view.label === null) continue
    if (!map.has(e.from_user)) map.set(e.from_user, e.view.label)
    if (!map.has(e.to_user)) map.set(e.to_user, e.view.label)
  }
  return map
})

// ---- 节点 data memo：成员/称谓/摘要未变时复用同一 data 引用，
// 避免 flowNodes 重算把新对象灌进每个节点造成全画布重渲染 ----

interface MemberNodeData {
  member?: Member
  viewLabel: string | null
  summary: boolean
}
const nodeDataMemo = new Map<number, MemberNodeData>()

function memberNodeData(id: number): MemberNodeData {
  const member = memberById.value.get(id)
  const viewLabel = labelByNode.value.get(id) ?? null
  const summary = gnodeById.value.get(id)?.visibility === 'lineage_summary'
  const cached = nodeDataMemo.get(id)
  if (cached && cached.member === member && cached.viewLabel === viewLabel && cached.summary === summary) {
    return cached
  }
  const fresh: MemberNodeData = { member, viewLabel, summary }
  nodeDataMemo.set(id, fresh)
  return fresh
}

const flowNodes = computed<FlowNode[]>(() => {
  const alive = new Set(layoutPositions.value.map((p) => p.id))
  for (const key of [...nodeDataMemo.keys()]) {
    if (!alive.has(key)) nodeDataMemo.delete(key)
  }
  return layoutPositions.value.map((p) => ({
    id: `n-${p.id}`,
    type: 'member',
    position: { x: p.x, y: p.y },
    data: memberNodeData(p.id),
  }))
})

// ---- 世代泳道（P3-3）：树状布局按 d3-hierarchy 分层输出横向底纹带节点 ----

const laneNodes = computed<FlowNode[]>(() => {
  if (mode.value !== 'tree') return []
  return computeGenerationLanes(layoutPositions.value).map((lane) => ({
    id: `gen-lane-${lane.generation}`,
    type: 'generation-lane',
    position: { x: lane.x, y: lane.y },
    data: { generation: lane.generation, width: lane.width, height: lane.height },
    draggable: false,
    selectable: false,
    connectable: false,
    deletable: false,
    focusable: false,
    zIndex: -1, // 沉到连线与节点之下（谱卷底纹，不可交互）
  }))
})

const flowNodesWithLanes = computed<FlowNode[]>(() => [...laneNodes.value, ...flowNodes.value])

// ---- 连线语义（P3-3）：confirmed 实线 / proposed 虚线 / disputed 朱砂虚线 ----

type EdgeFactState = 'confirmed' | 'proposed' | 'disputed'

/** 只读 store 已有数据，不新增请求：
 * disputed/proposed 取 kinship resolve 缓存里本人↔对方路径的 fact_state 摘要；
 * 无缓存数据时按边 active 状态兜底。 */
function edgeFactState(e: Relation): EdgeFactState {
  const spaceId = spaces.currentSpaceId
  const viewerId = auth.user?.id
  if (spaceId !== null && viewerId !== undefined) {
    const other =
      e.from_user === viewerId ? e.to_user : e.to_user === viewerId ? e.from_user : null
    const resolved = other === null ? null : kinship.cachedResolve(spaceId, viewerId, other)
    if (resolved) {
      if (resolved.fact_state.disputed > 0) return 'disputed'
      if (resolved.fact_state.proposed > 0) return 'proposed'
    }
  }
  return e.status === 'active' ? 'confirmed' : 'proposed'
}

const flowEdges = computed<FlowEdge[]>(() =>
  graph.edges.map((e) => ({
    id: `e-${e.id}`,
    source: `n-${e.from_user}`,
    target: `n-${e.to_user}`,
    label: e.view.label ?? undefined,
    class: `fg-edge fg-edge--${edgeFactState(e)}`,
    labelStyle: { fill: 'var(--fg-ink-secondary)', fontSize: '12px' },
    labelBgStyle: { fill: 'var(--fg-surface-raised)' },
    labelBgPadding: [6, 2] as [number, number],
    labelBgBorderRadius: 4,
  })),
)

/** fit-view 只围绕成员节点（世代泳道带更宽，不参与取景） */
function fitToMembers(): void {
  if (layoutPositions.value.length === 0) return
  void fitView({
    nodes: layoutPositions.value.map((p) => `n-${p.id}`),
    padding: 0.1,
  })
}

/** 列表布局：长幼排序 */
const listOrdered = computed(() => members.members)

function setMode(next: string | number | boolean | null): void {
  const nextMode = next as Mode
  localStorage.setItem('fg.layout', nextMode)
  mode.value = nextMode
}

function openProfile(id: number) {
  drawerMemberId.value = id
}

/** 确档状态徽章（与 MemberNode 同一投影规则：claimed ⇆ identity_confirmed） */
function identityBadge(member: Member): { text: string; cls: string } {
  return member.claim_status === 'claimed'
    ? { text: '已确档', cls: 'fg-badge fg-badge--confirmed' }
    : { text: '待确档', cls: 'fg-badge fg-badge--provisional' }
}

const spaceOptions = computed<SelectOption[]>(() =>
  spaces.spaces.map((s) => ({ label: s.name, value: s.id })),
)

// data-* 未收录进 Vue 的 HTML 属性类型，断言收窄；运行时 naive 原样透传到原生 input
const filterInputProps = {
  'data-test': 'canvas-filter-input',
  'aria-label': '空间内筛选',
} as VueInputHTMLAttributes
</script>

<template>
  <main class="space-view">
    <header class="topbar">
      <h1 class="title">你好，{{ auth.user?.name }}</h1>
      <GlobalSearch />
      <div class="actions">
        <NButton type="primary" data-test="go-home" @click="router.push('/home')">添加家人</NButton>
        <NButton v-if="auth.user?.is_admin" data-test="go-admin" @click="router.push('/admin')">管理</NButton>
        <NButton data-test="go-stats" @click="router.push('/stats')">统计</NButton>
        <NButton data-test="go-settings" @click="router.push('/settings')">设置</NButton>
        <NButton data-test="go-memory" @click="router.push('/memory')">记忆与知识</NButton>
      </div>
    </header>

    <!-- 家庭空间区 -->
    <section class="space-section" data-test="space-section">
      <NSelect
        v-if="spaces.spaces.length > 0"
        v-model:value="spaces.currentSpaceId"
        class="space-switcher"
        :options="spaceOptions"
        data-test="space-switcher"
        aria-label="切换家庭空间"
      />
      <NAlert v-else type="info" :show-icon="true" data-test="no-space-hint">
        你还没有家庭空间，先去「添加家人」创建吧。
      </NAlert>

      <!-- 待处理空间邀请 -->
      <div v-for="inv in spaces.pendingForMe" :key="inv.id" class="invite-row" data-test="space-invite">
        <span>收到加入「{{ spaces.spaces.find((s) => s.id === inv.space_id)?.name }}」的邀请</span>
        <span class="invite-actions">
          <NButton size="tiny" type="primary" data-test="accept-invite" @click="spaces.resolve(inv.id, 'accept')">接受</NButton>
          <NButton size="tiny" secondary data-test="reject-invite" @click="spaces.resolve(inv.id, 'reject')">拒绝</NButton>
        </span>
      </div>

      <!-- 空间 kind 徽标 + 治理入口（v2 §0.2/§0.5） -->
      <template v-if="spaces.currentSpace">
        <NTag
          size="small"
          :type="spaces.currentSpace.kind === 'lineage' ? 'success' : 'primary'"
          :bordered="true"
          data-test="space-kind-badge"
        >
          {{ spaces.currentSpace.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
        </NTag>
        <NTag v-if="myRole === 'guest'" size="small" type="warning" data-test="guest-badge">访客</NTag>
        <NButton size="small" secondary data-test="open-governance" @click="governanceOpen = true">空间管理</NButton>

        <!-- 待确档最小引用（AC-F2）：provisional 人物非正式成员，仅名字 -->
        <PendingProfileRefs :refs="spaces.profileRefs" />
      </template>
    </section>

    <!-- 关系查询（V2.3 KI-3）：自由文本解析；flag 关闭时组件自隐藏 -->
    <RelationLookup />

    <!-- 管家建议 Inbox（V2.4）：入口 + pending 徽章；403/503 自隐藏 -->
    <ActionCardInbox />

    <div class="filter-row">
      <NInput
        v-model:value="canvasFilter"
        class="filter-input"
        size="small"
        clearable
        placeholder="空间内筛选…"
        :input-props="filterInputProps"
      />
    </div>

    <!-- 视图范围切换（U3 家庭⇄家族） -->
    <div class="scope-switch" data-test="scope-switch">
      <NRadioGroup
        :value="viewScope"
        size="small"
        @update:value="(v: string | number | boolean | null) => (viewScope = v as 'family' | 'clan')"
      >
        <NRadioButton value="family">家庭空间</NRadioButton>
        <NRadioButton value="clan">家族空间</NRadioButton>
      </NRadioGroup>
    </div>

    <!-- 收到 owner 移交请求横幅（AC-F5） -->
    <NAlert v-if="incomingTransfer" type="warning" :show-icon="true" data-test="transfer-banner">
      「{{ spaces.spaces.find((s) => s.id === incomingTransfer?.space_id)?.name }}」的所有者请求把空间移交给你，请在「空间管理」中处理。
      <NButton size="tiny" type="warning" secondary class="inline-action" @click="governanceOpen = true">去处理</NButton>
    </NAlert>

    <!-- 待处理：空间邀请 / 连接请求（m2c） -->
    <section v-if="spaces.pendingForMe.length || graph.incoming.length" class="pending-section" data-test="pending-section">
      <div v-for="inv in spaces.pendingForMe" :key="`s-${inv.id}`" class="invite-row">
        <span>「{{ spaces.spaces.find((s) => s.id === inv.space_id)?.name }}」邀请你加入</span>
        <span class="invite-actions">
          <NButton size="tiny" type="primary" data-test="accept-invite" @click="spaces.resolve(inv.id, 'accept')">接受</NButton>
          <NButton size="tiny" secondary data-test="reject-invite" @click="spaces.resolve(inv.id, 'reject')">拒绝</NButton>
        </span>
      </div>
      <div v-for="rel in graph.incoming" :key="`r-${rel.id}`" class="invite-row" data-test="connection-request">
        <span>{{ memberName(rel.from_user) }} 想与你建立「{{ rel.view.dir_class }}」关系{{ rel.view.label ? `（${rel.view.label}）` : '' }}</span>
        <span class="invite-actions">
          <NButton size="tiny" type="primary" data-test="accept-relation" @click="graph.resolve(rel.id, 'accept')">接受</NButton>
          <NButton size="tiny" secondary data-test="reject-relation" @click="graph.resolve(rel.id, 'reject')">拒绝</NButton>
        </span>
      </div>
    </section>

    <!-- 我的连接（断连入口，D8） -->
    <section v-if="mode !== 'list' && graph.edges.length" class="relations-strip" data-test="relations-strip">
      <span class="strip-label">我的连接：</span>
      <NTag
        v-for="e in myEdges"
        :key="e.id"
        size="small"
        closable
        :type="e.status === 'pending' ? 'warning' : 'info'"
        @close="graph.revoke(e.id)"
      >
        {{ memberName(otherSide(e)) }}·{{ e.view.label ?? e.view.dir_class }}
      </NTag>
    </section>

    <!-- 布局切换器 -->
    <div class="layout-switch" data-test="layout-switch">
      <NRadioGroup :value="mode" size="small" @update:value="setMode">
        <NRadioButton value="canvas">自由摆放</NRadioButton>
        <NRadioButton value="tree">树状</NRadioButton>
        <NRadioButton value="list">列表</NRadioButton>
      </NRadioGroup>
    </div>

    <NAlert v-if="treeFailed" type="warning" closable data-test="tree-fallback-alert">
      当前关系较复杂，已切换为自由摆放。
    </NAlert>

    <!-- 画布 / 树状（谱卷：页面点阵透出 + 世代泳道底纹 + 语义连线） -->
    <section v-show="mode !== 'list'" class="canvas-section" data-test="canvas-section">
      <div class="canvas-wrap">
        <VueFlow
          :nodes="flowNodesWithLanes"
          :edges="flowEdges"
          fit-view-on-init
          data-test="flow-canvas"
          @node-drag-stop="handleNodeDragStop"
        >
          <Controls />
          <template #node-member="nodeProps">
            <MemberNode
              :id="nodeProps.id"
              :data="nodeProps.data"
              @open="openProfile"
              @join="requestJoin"
            />
          </template>
          <!-- 世代泳道底纹带：纯底纹节点（zIndex -1，不可交互） -->
          <template #node-generation-lane="laneProps">
            <div
              class="gen-lane"
              :data-generation="laneProps.data.generation"
              :style="{
                '--lane-width': `${laneProps.data.width}px`,
                '--lane-height': `${laneProps.data.height}px`,
              }"
            >
              <span class="gen-lane-label">第 {{ laneProps.data.generation }} 代</span>
            </div>
          </template>
        </VueFlow>
      </div>
      <!-- 空画布引导动作居中（规范红线：空状态必须给引导动作） -->
      <div v-if="graph.nodes.length === 0" class="canvas-empty" data-test="canvas-empty">
        <div class="canvas-empty-card">
          <NEmpty description="谱卷还未展开——先添加第一位家人吧">
            <NButton type="primary" data-test="empty-add-member" @click="router.push('/home')">
              添加第一位家人
            </NButton>
          </NEmpty>
        </div>
      </div>
    </section>

    <!-- 列表 -->
    <section v-if="mode === 'list'" class="list-view" data-test="list-view">
      <article
        v-for="m in listOrdered"
        :key="m.id"
        class="member-card"
        data-test="list-member-card"
        @click="openProfile(m.id)"
      >
        <span class="avatar" aria-hidden="true">{{ m.name.slice(0, 1) }}</span>
        <span class="member-name">{{ m.name }}</span>
        <span class="fg-badge card-badge" :class="identityBadge(m).cls">
          {{ identityBadge(m).text }}
        </span>
      </article>
    </section>

    <ProfileDrawer
      v-if="drawerMemberId !== null"
      :visible="true"
      :member-id="drawerMemberId"
      @close="drawerMemberId = null"
    />

    <SpaceGovernanceDialog v-model:visible="governanceOpen" />
  </main>
</template>

<style scoped>
.space-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 56px);
  box-sizing: border-box;
  padding: 20px;
  gap: 12px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
}

.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.space-section {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
}

.space-switcher {
  width: 200px;
}

.invite-row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent);
  border-radius: var(--fg-radius-control);
}

.invite-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.inline-action {
  margin-left: 8px;
  vertical-align: middle;
}

.layout-switch {
  display: flex;
}

.filter-row {
  display: flex;
}

.filter-input {
  width: 220px;
}

.pending-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.relations-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.strip-label {
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

/* 画布容器：不铺自绘底色——页面点阵（tokens.css body 背景）透出即为画布底纹，
   避免 Vue Flow Background 与页面点阵双层打架（P3-1） */
.canvas-section {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 420px;
}

.canvas-wrap {
  position: relative;
  flex: 1;
  min-height: 400px;
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  overflow: hidden;
}

/* 世代泳道底纹带（P3-3）：树状模式下的"谱卷"横带，尺寸经 CSS 变量注入 */
.gen-lane {
  display: flex;
  align-items: center;
  width: var(--lane-width);
  height: var(--lane-height);
  background-color: color-mix(in srgb, var(--fg-surface-sunken) 62%, transparent);
  border-top: 1px solid var(--fg-line);
  border-bottom: 1px solid var(--fg-line);
}

.gen-lane-label {
  margin-left: 16px;
  padding: 1px 10px;
  font-size: 12px;
  letter-spacing: 0.12em;
  color: var(--fg-ink-faint);
  background-color: color-mix(in srgb, var(--fg-surface-raised) 80%, transparent);
  border: 1px solid var(--fg-line);
  border-radius: 999px;
}

/* 连线语义样式（P3-3）：经 edge.class 作用于 Vue Flow 内部 path（scopeId 不可达 → :deep） */
.canvas-wrap :deep(.fg-edge--confirmed .vue-flow__edge-path) {
  stroke: var(--fg-ink-secondary);
  stroke-width: 1.5;
}

.canvas-wrap :deep(.fg-edge--proposed .vue-flow__edge-path) {
  stroke: var(--fg-status-proposed);
  stroke-width: 1.5;
  stroke-dasharray: 7 5;
}

.canvas-wrap :deep(.fg-edge--disputed .vue-flow__edge-path) {
  stroke: var(--fg-status-disputed);
  stroke-width: 1.5;
  stroke-dasharray: 3 5;
}

/* Controls 重样式：结构样式来自 @vue-flow/core/dist/style.css 之外的部分全部 token 自绘 */
.canvas-wrap :deep(.vue-flow__controls) {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
  box-shadow: var(--fg-shadow-card);
}

.canvas-wrap :deep(.vue-flow__controls-button) {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: none;
  border-bottom: 1px solid var(--fg-line);
  background-color: var(--fg-surface-raised);
  color: var(--fg-ink-secondary);
  cursor: pointer;
}

.canvas-wrap :deep(.vue-flow__controls-button:last-child) {
  border-bottom: none;
}

.canvas-wrap :deep(.vue-flow__controls-button:hover) {
  background-color: var(--fg-accent-soft);
  color: var(--fg-accent);
}

.canvas-wrap :deep(.vue-flow__controls-button:disabled) {
  color: var(--fg-ink-faint);
  cursor: default;
}

.canvas-wrap :deep(.vue-flow__controls-button svg) {
  width: 14px;
  height: 14px;
}

/* 空画布引导：居中卡片式空状态（规范红线） */
.canvas-empty {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.canvas-empty-card {
  padding: 28px 40px;
  background-color: var(--fg-surface-raised);
  border: 1px dashed var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  pointer-events: auto;
}

/* 列表布局成员卡：与 HomeView 同一立牌隐喻 */
.list-view {
  flex: 1;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.member-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  transition:
    box-shadow 0.2s,
    border-color 0.2s;
}

.member-card:hover {
  border-color: var(--fg-accent);
  box-shadow: var(--fg-shadow-raised);
}

.member-card .avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  font-family: var(--fg-font-display);
  font-size: 17px;
  font-weight: 700;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: var(--fg-radius-control);
}

[data-theme='modern'] .member-card .avatar {
  border-radius: 999px;
}

.member-name {
  font-family: var(--fg-font-display);
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.card-badge {
  margin-left: auto;
}
</style>
