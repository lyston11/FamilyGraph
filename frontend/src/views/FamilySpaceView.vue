<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { VueFlow, useVueFlow, type Edge as FlowEdge, type Node as FlowNode } from '@vue-flow/core'

import GlobalSearch from '@/components/common/GlobalSearch.vue'
import MemberNode from '@/components/canvas/MemberNode.vue'
import PendingProfileRefs from '@/components/member/PendingProfileRefs.vue'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import SpaceGovernanceDialog from '@/components/member/SpaceGovernanceDialog.vue'
import { computeCanvasLayout, computeTreeLayout, type PositionedNode } from '@/composables/useLayout'
import { useAuthStore } from '@/stores/auth'
import { useGraphStore } from '@/stores/graph'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import { getSpacePositions, putSpacePositions } from '@/api/spaces'
import type { Relation } from '@/types/api'
import type { LayoutNodeInput } from '@/composables/useLayout'
import type { LayoutMode } from '@/types/layout'

/**
 * 家庭空间页（m1d，M1 收口）：卡片画布 + 三种布局一键切换。
 * - 画布拖拽：位置经 positions API 持久化（node_positions）
 * - 树状：computeTreeLayout，失败回退画布模式并提示
 * - 列表：长幼排序（生日升序，缺失按 id——Q1 默认方案）
 */
const auth = useAuthStore()
const members = useMembersStore()
const spaces = useSpacesStore()
const graph = useGraphStore()
const router = useRouter()

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
  async (id) => {
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
    ElMessage.success('加入申请已发送，等待对方确认')
  } catch {
    ElMessage.error('申请发送失败，请稍后重试')
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

const layoutPositions = ref<PositionedNode[]>([])
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
    setTimeout(() => void fitView(), 30)
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
  const map = new Map<number, ReturnType<typeof Object>>([])
  for (const m of members.members) map.set(m.id, m)
  return map
})

const flowNodes = computed<FlowNode[]>(() =>
  layoutPositions.value.map((p) => {
    const gnode = graph.nodes.find((n) => n.id === p.id)
    const member = members.members.find((m) => m.id === p.id)
    const edge = graph.edges.find(
      (e) => (e.from_user === p.id || e.to_user === p.id) && e.view.label !== null,
    )
    void memberById.value
    return {
      id: `n-${p.id}`,
      type: 'member',
      position: { x: p.x, y: p.y },
      data: {
        member,
        viewLabel: edge?.view.label ?? null,
        summary: gnode?.visibility === 'lineage_summary',
      },
    }
  }),
)

const flowEdges = computed<FlowEdge[]>(() =>
  graph.edges.map((e) => ({
    id: `e-${e.id}`,
    source: `n-${e.from_user}`,
    target: `n-${e.to_user}`,
    label: e.view.label ?? undefined,
    style: { stroke: 'var(--el-border-color)' },
  })),
)

/** 列表布局：长幼排序 */
const listOrdered = computed(() => members.members)

function setMode(next: Mode) {
  localStorage.setItem('fg.layout', next)
  mode.value = next
}

function openProfile(id: number) {
  drawerMemberId.value = id
}

void router
</script>

<template>
  <main class="space-view">
    <header class="topbar">
      <h1 class="title">你好，{{ auth.user?.name }}</h1>
      <GlobalSearch />
      <div class="actions">
        <el-button type="primary" data-test="go-home" @click="router.push('/home')">添加家人</el-button>
        <el-button v-if="auth.user?.is_admin" data-test="go-admin" @click="router.push('/admin')">管理</el-button>
        <el-button data-test="go-stats" @click="router.push('/stats')">统计</el-button>
        <el-button data-test="go-settings" @click="router.push('/settings')">设置</el-button>
      </div>
    </header>

    <!-- 家庭空间区 -->
    <section class="space-section" data-test="space-section">
      <el-select
        v-if="spaces.spaces.length > 0"
        v-model="spaces.currentSpaceId"
        data-test="space-switcher"
        style="width: 200px"
      >
        <el-option v-for="s in spaces.spaces" :key="s.id" :label="s.name" :value="s.id" />
      </el-select>
      <el-alert v-else type="info" :closable="false" data-test="no-space-hint">
        你还没有家庭空间，先去「添加家人」创建吧。
      </el-alert>

      <!-- 待处理空间邀请 -->
      <div v-for="inv in spaces.pendingForMe" :key="inv.id" class="invite-row" data-test="space-invite">
        <span>收到加入「{{ spaces.spaces.find((s) => s.id === inv.space_id)?.name }}」的邀请</span>
        <el-button size="small" type="primary" data-test="accept-invite" @click="spaces.resolve(inv.id, 'accept')">接受</el-button>
        <el-button size="small" data-test="reject-invite" @click="spaces.resolve(inv.id, 'reject')">拒绝</el-button>
      </div>

      <!-- 空间 kind 徽标 + 治理入口（v2 §0.2/§0.5） -->
      <template v-if="spaces.currentSpace">
        <el-tag
          size="small"
          :type="spaces.currentSpace.kind === 'lineage' ? 'success' : 'primary'"
          data-test="space-kind-badge"
        >
          {{ spaces.currentSpace.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
        </el-tag>
        <el-tag v-if="myRole === 'guest'" size="small" type="warning" data-test="guest-badge">访客</el-tag>
        <el-button size="small" data-test="open-governance" @click="governanceOpen = true">空间管理</el-button>

        <!-- 待确档最小引用（AC-F2）：provisional 人物非正式成员，仅名字 -->
        <PendingProfileRefs :refs="spaces.profileRefs" />
      </template>
    </section>

    <div class="filter-row">
      <input
        v-model="canvasFilter"
        class="filter-input"
        placeholder="空间内筛选…"
        aria-label="空间内筛选"
        data-test="canvas-filter-input"
      />
    </div>

    <!-- 视图范围切换（U3 家庭⇄家族） -->
    <div class="scope-switch" data-test="scope-switch">
      <el-radio-group :model-value="viewScope" @update:model-value="(v: 'family' | 'clan') => (viewScope = v)">
        <el-radio-button value="family">👨‍👩‍👧 家庭空间</el-radio-button>
        <el-radio-button value="clan">🌲 家族空间</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 收到 owner 移交请求横幅（AC-F5） -->
    <el-alert
      v-if="incomingTransfer"
      type="warning"
      :closable="false"
      data-test="transfer-banner"
    >
      「{{ spaces.spaces.find((s) => s.id === incomingTransfer?.space_id)?.name }}」的所有者请求把空间移交给你，请在「空间管理」中处理。
      <el-button size="small" type="warning" plain @click="governanceOpen = true">去处理</el-button>
    </el-alert>

    <!-- 待处理：空间邀请 / 连接请求（m2c） -->
    <section v-if="spaces.pendingForMe.length || graph.incoming.length" class="pending-section" data-test="pending-section">
        <div v-for="inv in spaces.pendingForMe" :key="`s-${inv.id}`" class="invite-row">
          <span>「{{ spaces.spaces.find((s) => s.id === inv.space_id)?.name }}」邀请你加入</span>
          <el-button size="small" type="primary" data-test="accept-invite" @click="spaces.resolve(inv.id, 'accept')">接受</el-button>
          <el-button size="small" data-test="reject-invite" @click="spaces.resolve(inv.id, 'reject')">拒绝</el-button>
        </div>
        <div v-for="rel in graph.incoming" :key="`r-${rel.id}`" class="invite-row" data-test="connection-request">
          <span>{{ memberName(rel.from_user) }} 想与你建立「{{ rel.view.dir_class }}」关系{{ rel.view.label ? `（${rel.view.label}）` : '' }}</span>
          <el-button size="small" type="primary" data-test="accept-relation" @click="graph.resolve(rel.id, 'accept')">接受</el-button>
          <el-button size="small" data-test="reject-relation" @click="graph.resolve(rel.id, 'reject')">拒绝</el-button>
        </div>
      </section>

    <!-- 我的连接（断连入口，D8） -->
    <section v-if="mode !== 'list' && graph.edges.length" class="relations-strip" data-test="relations-strip">
      <span class="strip-label">我的连接：</span>
      <el-tag
        v-for="e in myEdges"
        :key="e.id"
        closable
        :type="e.status === 'pending' ? 'warning' : 'info'"
        @close="graph.revoke(e.id)"
      >
        {{ memberName(otherSide(e)) }}·{{ e.view.label ?? e.view.dir_class }}
      </el-tag>
    </section>

    <!-- 布局切换器 -->
    <div class="layout-switch" data-test="layout-switch">
      <el-radio-group :model-value="mode" @update:model-value="setMode">
        <el-radio-button value="canvas">🎨 自由摆放</el-radio-button>
        <el-radio-button value="tree">🌳 树状</el-radio-button>
        <el-radio-button value="list">📋 列表</el-radio-button>
      </el-radio-group>
    </div>

    <el-alert v-if="treeFailed" type="warning" :closable="true" data-test="tree-fallback-alert">
      当前关系较复杂，已切换为自由摆放。
    </el-alert>

    <!-- 画布 / 树状 -->
    <section v-show="mode !== 'list'" class="canvas-wrap">
      <VueFlow :nodes="flowNodes" :edges="flowEdges" fit-view-on-init data-test="flow-canvas" @node-drag-stop="handleNodeDragStop">
        <Background />
        <Controls />
        <template #node-member="nodeProps">
          <MemberNode
            :id="nodeProps.id"
            :data="nodeProps.data"
            @open="openProfile"
            @join="requestJoin"
          />
        </template>
      </VueFlow>
      <el-empty v-if="graph.nodes.length === 0" description="空空如也，先添加家人吧" />
    </section>

    <!-- 列表 -->
    <section v-if="mode === 'list'" class="list-view" data-test="list-view">
      <el-card
        v-for="m in listOrdered"
        :key="m.id"
        shadow="hover"
        class="member-card"
        data-test="list-member-card"
        @click="openProfile(m.id)"
      >
        {{ m.name }}
      </el-card>
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
  height: calc(100vh - 40px);
  padding: 20px;
  gap: 12px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.title {
  font-size: 18px;
}

.actions {
  display: flex;
  gap: 8px;
}

.space-section {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
}

.invite-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.layout-switch {
  display: flex;
}

.filter-row {
  display: flex;
}

.filter-input {
  padding: 6px 10px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  width: 220px;
}

.canvas-wrap {
  flex: 1;
  min-height: 400px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
  overflow: hidden;
  position: relative;
}

.list-view {
  flex: 1;
  overflow: auto;
}
</style>
