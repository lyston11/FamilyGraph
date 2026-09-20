<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NSpin } from 'naive-ui'

import RelationshipDetailPanel from '@/components/canvas/RelationshipDetailPanel.vue'
import { pathClassLabel } from '@/components/canvas/relationshipDisplay'
import MaskedField from '@/components/common/MaskedField.vue'
import FamilySpaceJoinDialog from '@/components/member/FamilySpaceJoinDialog.vue'
import KinshipTermPanel from '@/components/kinship/KinshipTermPanel.vue'
import { useSpaceContext } from '@/composables/useSpaceContext'
import { usePersonalFamilyViewPolling } from '@/composables/usePersonalFamilyViewPolling'
import { ApiError } from '@/api/errors'
import { useActionCardsStore } from '@/stores/actionCards'
import { useAuthStore } from '@/stores/auth'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import { isMasked } from '@/types/api'
import { isTerminalCardState } from '@/types/actionCard'
import type {
  ClaimStatus,
  GenderType,
  Maskable,
  PersonalFamilyViewDisplay,
  PersonalFamilyViewEdge,
  PersonalFamilyViewNode,
  PrivacyMode,
} from '@/types/api'

/**
 * 他人只读公示页 / PersonProfileView（09-01 design.md §5.3、PRD §2.4，路由
 * /people/:userId）。
 *
 * 数据与安全边界（红线）：
 * - 只接受路由参数 userId；当前 lineage 的 space_id 来自 spaces/useSpaceContext
 *   会话上下文，不接受任何 viewer/root 路由参数；
 * - 直达/刷新：先确保当前 lineage 的 PersonalFamilyView 快照已加载（store.load），
 *   再在快照内查 getVisiblePerson——目标只从当前授权快照查询，禁止调用 /users
 *   或任何按 userId 的宽泛用户详情接口；
 * - 快照加载失败（网络等）→ 安全失败状态；目标不在快照中（不存在/不可见/被
 *   撤权）与投影端点 403/404 → 统一「对方不可见或不存在」安全状态，不显示
 *   目标 ID、空间名、路径长度或任何隐藏占位/数量信息（防枚举探测）；
 * - 页面只读：不提供修改对方资料/建立关系/查看对方家庭/扩大权限的任何按钮；
 *   09-20 需求修订：新增「加入家庭空间」入口，限定在**当前家族空间**内双向——
 *   邀请对方进入我在此家族下的家庭空间，或申请加入对方在此家族下的家庭空间；
 *   两个方向都只产生 pending（邀请需对方接受，申请需该空间管理员批准）。
 *   已有相关 ActionCard 时仍只提供「查看待办」跳转（→ /notifications）；
 *   Bridge pending 只在通知/待办处理，本页不渲染 approve/reject/consent/revoke
 *   等 Bridge 操作控件；
 * - 「返回家族树」只做路由导航（name family-space），保持同一 lineage 空间
 *   上下文；刷新后的空间上下文由 spaces store 会话态决定，不把敏感节点写进 URL；
 * - 点击自己（userId === 当前用户）不进入本页：页面级处理 → 重定向 /（家庭卡）。
 */

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const pfv = usePersonalFamilyViewStore()
const actionCards = useActionCardsStore()
const spaceContext = useSpaceContext()

type PagePhase = 'loading' | 'ready' | 'error' | 'unavailable'

const phase = ref<PagePhase>('loading')
const selectedEdgeKey = ref<string | null>(null)
let profileRequest = 0
let mounted = false

/** 路由参数 userId：非法（非正整数）一律视为不可见目标，不回显原始参数 */
const targetUserId = computed<number | null>(() => {
  const raw = route.params.userId
  const parsed = typeof raw === 'string' ? Number(raw) : NaN
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
})

/** 点击自己不进入本页：页面级处理 → 重定向家庭卡（design.md §2.4） */
const isSelf = computed(
  () => targetUserId.value !== null && targetUserId.value === (auth.user?.id ?? null),
)

watch(
  isSelf,
  (self) => {
    if (self) void router.replace({ name: 'home' })
  },
  { immediate: true },
)

const spaceId = computed(() => spaces.currentSpaceId)
const pollEnabled = ref(false)
const live = usePersonalFamilyViewPolling(spaceId, pollEnabled, { autoLoad: false })
const kinshipViewVersion = computed(() => {
  const data = live.data.value
  if (data === null) return undefined
  return data.progress ? `generation:${data.progress.generation}` : `view:${data.view_version}`
})
const preparing = computed(() => {
  const data = live.data.value
  return data !== null && data.nodes.length === 0 && (
    data.progress ? ['queued', 'preparing', 'building', 'retrying'].includes(data.progress.phase)
      : ['never_computed', 'queued', 'running'].includes(data.status))
})

/** 目标节点：只从当前授权快照内查询，快照更新（撤权/刷新）时响应式回收 */
const profileNode = computed<PersonalFamilyViewNode | null>(() => {
  const target = targetUserId.value
  const sid = spaceId.value
  if (target === null || sid === null) return null
  return pfv.getVisiblePerson(sid, target)
})

/** 统一安全不可见状态：显式不可见，或快照刷新后目标已不在授权快照中 */
const unavailableVisible = computed(
  () => phase.value === 'unavailable' || (phase.value === 'ready' && profileNode.value === null && !preparing.value),
)

/**
 * 直达/刷新流程：先建立空间上下文（spaces/useSpaceContext 会话态），再确保当前
 * lineage 的授权快照已加载，最后在快照内验证目标。加载失败区分「安全失败（可重试）」
 * 与「不可见或不存在（403/404 同形状合并，不可区分探测）」。
 */
async function ensureProfile(): Promise<void> {
  // 目标非法（null）或命中自己：不建立任何数据上下文（自己走重定向处理）
  if (targetUserId.value === null || isSelf.value) return
  const request = ++profileRequest
  const current = () => mounted && request === profileRequest
  pollEnabled.value = false
  selectedEdgeKey.value = null
  phase.value = 'loading'
  try {
    // 空间列表缺失（硬刷新直达）时先补拉；失败进入安全失败状态
    if (spaces.spaces.length === 0) {
      await spaces.load()
    }
  } catch {
    if (current()) phase.value = 'error'
    return
  }
  if (!current()) return
  // 空间上下文：会话内到达（家族树/家庭卡）沿用当前空间，绝不重跑默认选择——
  // ensureDefaultSpace 会把上下文改回最近 household，导致从 lineage 树进入时
  // 目标意外「不可见」（走查实测）。仅硬刷新直达（无上下文）时兜底选择一次。
  // 09-05 R1 扩展：household 上下文同样走通用授权快照（/personal-family-view 按
  // active 成员资格授权，与 lineage 同一安全合同）——家庭卡点成员卡现在能看到
  // 对方资料；其余未知 kind 仍安全不可见。
  if (spaces.currentSpaceId === null) {
    const kind = await spaceContext.ensureDefaultSpace().catch(() => 'none' as const)
    if (!current()) return
    if (kind === 'none' || spaceId.value === null) {
      phase.value = 'unavailable'
      return
    }
  } else if (spaces.currentSpace?.kind !== 'lineage' && spaces.currentSpace?.kind !== 'household') {
    phase.value = 'unavailable'
    return
  }
  const contextSpaceId = spaceId.value
  if (contextSpaceId === null) {
    phase.value = 'unavailable'
    return
  }
  pollEnabled.value = true
  await live.refresh()
  if (!current()) return
  syncProfilePhase()
  // 相关待办（ActionCard）经 store 静默加载；入口降级（403/503）自然无按钮
  const sid = spaceId.value
  if (sid !== null) void actionCards.ensureLoaded(sid).catch(() => undefined)
}

onMounted(() => {
  mounted = true
  if (!isSelf.value) void ensureProfile()
})

onBeforeUnmount(() => { mounted = false; profileRequest += 1 })

watch([targetUserId, spaceId], () => {
  // null = 非法参数或离开本页的瞬态（如自己重定向）：不发起任何加载
  if (targetUserId.value === null || isSelf.value) return
  void ensureProfile()
})

function syncProfilePhase(): void {
  if (!pollEnabled.value || spaceId.value === null) return
  const cause = pfv.errorFor(spaceId.value)
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) phase.value = 'unavailable'
  else if (live.data.value !== null) phase.value = 'ready'
  else if (cause !== null) phase.value = 'error'
}

watch([live.data, () => spaceId.value === null ? null : pfv.errorFor(spaceId.value)], syncProfilePhase)
watch([profileNode, () => live.data.value?.progress?.generation], () => {
  if (pollEnabled.value && spaceId.value !== null && targetUserId.value !== null && profileNode.value) {
    void pfv.focusTarget(spaceId.value, targetUserId.value).catch(() => undefined)
  }
})

// ---- 只读展示：身份头部 / 公示字段 / 关系上下文 ----

const LEVEL_LABELS = {
  self_private: '仅本人可见',
  household_detail: '家庭详情可见',
  lineage_summary: '族谱摘要 · 不可展开',
} as const

const levelText = computed(() =>
  profileNode.value === null ? '' : LEVEL_LABELS[profileNode.value.visibility_level],
)

type ProfileFieldKey = 'gender' | 'birth' | 'death' | 'bio' | 'privacy_mode' | 'claim_status'

interface ProfileFieldRow {
  key: ProfileFieldKey
  label: string
  /** Maskable 原值（masked/日期/明文文本）：交给 MaskedField 统一渲染 */
  value: unknown
  /** 明文枚举的中文文案；null = 交给 MaskedField 渲染（遮罩/日期/不详） */
  text: string | null
}

function enumRow<T extends string>(
  key: ProfileFieldKey,
  label: string,
  value: Maskable<T>,
  toText: (clear: T) => string,
): ProfileFieldRow {
  return { key, label, value, text: isMasked(value) ? null : toText(value) }
}

function valueRow(key: ProfileFieldKey, label: string, value: unknown): ProfileFieldRow {
  return { key, label, value, text: null }
}

/** 已授权公示字段：masked 用 MaskedField 锁形章；头像以姓字纸牌表达（同既有页面） */
const profileRows = computed<ProfileFieldRow[]>(() => {
  const display: PersonalFamilyViewDisplay | null = profileNode.value?.display ?? null
  if (display === null) return []
  return [
    enumRow<GenderType>('gender', '性别', display.gender, (gender) =>
      gender === 'f' ? '女' : gender === 'm' ? '男' : '不详',
    ),
    valueRow('birth', '出生', display.birth),
    valueRow('death', '逝世', display.death),
    valueRow('bio', '简介', display.bio),
    enumRow<PrivacyMode>('privacy_mode', '档案管理', display.privacy_mode, (mode) =>
      mode === 'handover' ? '移交本人' : '永久管理',
    ),
    enumRow<ClaimStatus>('claim_status', '档案状态', display.claim_status, (status) =>
      status === 'claimed' ? '已确档' : '待确档',
    ),
  ]
})

/** 当前用户可见的关系上下文：快照 edges 中与目标相邻的边（全部为已授权投影） */
const adjacentEdges = computed<PersonalFamilyViewEdge[]>(() => {
  const target = targetUserId.value
  const sid = spaceId.value
  const data = sid === null ? null : pfv.forSpace(sid)
  if (target === null || data === null) return []
  return data.edges.filter((edge) => edge.from_user_id === target || edge.to_user_id === target)
})

const targetProgress = computed(() => spaceId.value === null || targetUserId.value === null
  ? null : pfv.targetFor(spaceId.value, targetUserId.value))
const targetProgressMessage = computed(() => {
  switch (targetProgress.value?.status) {
    case 'pending': return '正在优先整理与这位家人的关系，称谓和完整说明准备好后会自动显示。'
    case 'unavailable': return '已完成整理，目前没有可显示的称谓。已授权的公示资料仍可查看。'
    case 'failed': return '这位家人的称谓暂未整理成功，已授权的公示资料仍可查看。可以重新尝试。'
    default: return ''
  }
})

function relationKey(edge: PersonalFamilyViewEdge): string {
  return `${edge.from_user_id}:${edge.to_user_id}:${edge.edge_kind}`
}
const selectedEdge = computed(() => adjacentEdges.value.find((edge) => relationKey(edge) === selectedEdgeKey.value) ?? null)
watch(adjacentEdges, (edges) => {
  if (!edges.some((edge) => relationKey(edge) === selectedEdgeKey.value)) selectedEdgeKey.value = null
})

/** 快照内 user_id → display.name；解析不到返回 null（面板安全占位） */
function resolveName(userId: number): string | null {
  const sid = spaceId.value
  if (sid === null) return null
  return pfv.forSpace(sid)?.nodes.find((node) => node.user_id === userId)?.display.name ?? null
}

const snapshotMeta = computed(() => {
  const sid = spaceId.value
  return sid === null ? null : pfv.forSpace(sid)
})

function openRelationDetail(edge: PersonalFamilyViewEdge): void {
  selectedEdgeKey.value = relationKey(edge)
  // 面板为页面顶部覆盖层（与家族树一致）：打开时回到页首保证可见
  window.scrollTo({ top: 0, behavior: 'auto' })
}

function closeRelationDetail(): void {
  selectedEdgeKey.value = null
}

// ---- 相关 ActionCard：只提供「查看待办」跳转，无任何写操作 ----

const hasRelatedTodos = computed(() => {
  const target = targetUserId.value
  const sid = spaceId.value
  if (target === null || sid === null) return false
  return (
    actionCards.cardsOf(sid).filter(
      (card) =>
        !isTerminalCardState(card.state) &&
        (card.subject_user.id === target || card.object_user?.id === target),
    ).length > 0
  )
})

function goNotifications(): void {
  void router.push({ name: 'notifications' })
}

// ---- 09-20：家族空间内双向加入（邀请 / 申请；都只产生 pending） ----

const joinOpen = ref(false)

/**
 * 当前上下文对应的家族空间 id。
 *
 * - 家族树上下文：当前空间本身就是该家族空间；
 * - 家庭卡上下文：用该家庭空间配对的家族空间（`lineageForSpace`，显式配对优先）；
 * - 解析不出（未配对/多义）→ null，不显示入口（不做本地猜测）。
 */
const lineageSpaceId = computed<number | null>(() => {
  const current = spaces.currentSpace
  if (current === null) return null
  if (current.kind === 'lineage') return current.id
  return spaces.lineageForSpace(current.id)?.id ?? null
})

/** 只在目标可见、且能确定当前家族空间时提供入口。 */
const canJoinFamilySpace = computed(
  () => profileNode.value !== null && lineageSpaceId.value !== null,
)

function openJoin(): void {
  joinOpen.value = true
}

/**
 * 上下文感知返回（09-05 PRD R1）：进入方经 router state 携 `fgBackTo`
 * （家庭卡='home' / 家族树='family-space'）；state 缺失（直达/刷新）兜底家庭卡。
 */
type ProfileBackTarget = 'home' | 'family-space'

/** 点击时读取（history.state 非响应式）；route.fullPath 仅用于标签重算 */
function currentBackTarget(): ProfileBackTarget {
  const fromState = (history.state as Record<string, unknown> | null)?.fgBackTo
  return fromState === 'family-space' ? 'family-space' : 'home'
}

const backTarget = computed<ProfileBackTarget>(() => {
  void route.fullPath
  return currentBackTarget()
})

const backLabel = computed(() => (backTarget.value === 'family-space' ? '返回家族树' : '返回家庭卡'))

function goBack(): void {
  void router.push({ name: currentBackTarget() })
}

async function retry(): Promise<void> {
  if (!pollEnabled.value) { await ensureProfile(); return }
  await live.retry()
  if (mounted) syncProfilePhase()
}
</script>

<template>
  <main class="person-profile-view" data-test="person-profile-view">
    <!-- 09-06 视觉补齐：与家庭首页 family-space-hero 同套大卡设计语言 -->
    <article class="profile-hero">
    <!-- 顶部返回：回进入来源（家庭卡/家族树），state 缺失兜底家庭卡 -->
    <div class="back-row">
      <NButton quaternary size="small" class="back-button" data-test="back-to-family-tree" @click="goBack">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 5H8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h6" />
          <path d="m13 8 4 4-4 4M17 12H9" />
        </svg>
        {{ backLabel }}
      </NButton>
    </div>

    <NSpin v-if="phase === 'loading'" class="loading-spin" :show="true" />

    <!-- 快照加载失败：安全失败状态，不显示空间名/目标 ID -->
    <section v-else-if="phase === 'error'" class="status-panel" data-test="profile-load-error">
      <h2 class="status-title">暂时无法读取</h2>
      <p class="status-text">个人公示页请求失败，已按安全策略隐藏空间与目标信息，稍后可重试。</p>
      <NButton size="small" data-test="profile-retry" @click="retry">重新加载</NButton>
    </section>

    <section v-else-if="preparing" class="status-panel" data-test="profile-preparing">
      <h2 class="status-title">正在准备家谱</h2>
      <p class="status-text" role="status">{{ live.progressMessage.value || '授权家谱准备好后，资料会自动显示。' }}</p>
      <p v-if="live.notice.value" class="status-text" role="status" data-test="profile-preparing-notice">{{ live.notice.value }}</p>
      <NButton size="small" @click="retry">重新加载</NButton>
    </section>

    <!-- 目标不在授权快照中 / 投影 403/404：统一安全状态，无任何目标细节 -->
    <section v-else-if="unavailableVisible" class="status-panel" data-test="profile-unavailable">
      <h2 class="status-title">对方不可见或不存在</h2>
      <p class="status-text">对方资料未在当前授权范围内公开，或不存在。</p>
    </section>

    <template v-else-if="profileNode">
      <NAlert v-if="live.notice.value" type="warning" :show-icon="true" data-test="profile-update-notice">
        {{ live.notice.value }}
        <NButton size="small" @click="retry">重新加载</NButton>
      </NAlert>
      <p v-if="live.data.value?.progress" class="status-text" role="status" data-test="profile-progress">
        {{ live.progressMessage.value }}
      </p>
      <!-- 身份头部：头像（姓字纸牌，与既有页面一致）、姓名、可见性说明（icon+文字） -->
      <section class="identity-card" data-test="profile-identity">
        <span class="avatar" aria-hidden="true">{{ profileNode.display.name.slice(0, 1) }}</span>
        <div class="identity-main">
          <h1 class="identity-name" data-test="profile-name">{{ profileNode.display.name }}</h1>
          <span class="level-badge" data-test="profile-visibility">
            <svg
              v-if="profileNode.visibility_level === 'lineage_summary'"
              class="badge-icon"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle cx="12" cy="5" r="2" />
              <circle cx="6" cy="18" r="2" />
              <circle cx="18" cy="18" r="2" />
              <path d="M12 7v3.5M6 16v-1.2a1.8 1.8 0 0 1 1.8-1.8h8.4A1.8 1.8 0 0 1 18 14.8V16" />
            </svg>
            <svg v-else class="badge-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3a5 5 0 0 0-5 5v2H6a2 2 0 0 0-2 2v8h16v-8a2 2 0 0 0-2-2h-1V8a5 5 0 0 0-5-5Zm-3 7V8a3 3 0 1 1 6 0v2H9Z" />
            </svg>
            {{ levelText }}
          </span>
        </div>
      </section>

      <!-- 已授权公示字段：masked 用 MaskedField 锁形章 -->
      <section class="fields-card" data-test="profile-fields">
        <h2 class="section-title">公示资料</h2>
        <dl class="field-list">
          <div
            v-for="row in profileRows"
            :key="row.key"
            class="field-row"
            :data-test="`profile-field-${row.key}`"
          >
            <dt class="field-label">{{ row.label }}</dt>
            <dd class="field-value">
              <MaskedField v-if="row.text === null" :value="row.value" />
              <span v-else>{{ row.text }}</span>
            </dd>
          </div>
        </dl>
      </section>

      <!-- 先由授权快照确认目标可见，再开放本人的称谓偏好入口。 -->
      <KinshipTermPanel
        :key="`${auth.user?.id}:${spaceId}:${profileNode.user_id}`"
        :member-id="profileNode.user_id"
        :view-version="kinshipViewVersion"
      />

      <!-- 当前用户可见的关系上下文：只读，点击打开关系说明面板 -->
      <section class="relations-card" data-test="profile-relations">
        <h2 class="section-title">关系上下文</h2>
        <p v-if="targetProgressMessage" class="status-text" role="status" :data-test="`profile-target-${targetProgress?.status}`">
          {{ targetProgressMessage }}
        </p>
        <NButton v-if="targetProgress?.status === 'failed'" size="small" data-test="profile-target-retry" @click="retry">重新尝试</NButton>
        <p v-if="adjacentEdges.length === 0 && !targetProgressMessage" class="status-text" data-test="profile-relations-empty">
          当前授权快照中没有与该成员可见的关系路径。
        </p>
        <template v-if="adjacentEdges.length > 0">
          <button
            v-for="(edge, edgeIndex) in adjacentEdges"
            :key="`${edge.from_user_id}-${edge.to_user_id}-${edgeIndex}`"
            type="button"
            class="relation-row"
            :data-test="`profile-relation-${edgeIndex}`"
            @click="openRelationDetail(edge)"
          >
            <span class="relation-term">{{ edge.term ?? '暂无称谓' }}</span>
            <span class="relation-class">{{ pathClassLabel(edge.path_class) }}</span>
            <span class="relation-entry">查看关系说明</span>
          </button>
        </template>
        <div class="relations-actions">
          <NButton
            v-if="canJoinFamilySpace"
            size="small"
            type="primary"
            secondary
            data-test="profile-family-space-join"
            @click="openJoin"
          >
            加入家庭空间
          </NButton>
          <NButton
            v-if="hasRelatedTodos"
            size="small"
            secondary
            data-test="profile-view-todos"
            @click="goNotifications"
          >
            查看待办
          </NButton>
        </div>
      </section>
    </template>
    </article>

    <!-- 09-20：家族空间内双向加入（邀请/申请，都只产生 pending） -->
    <FamilySpaceJoinDialog
      v-if="profileNode !== null && lineageSpaceId !== null"
      v-model:visible="joinOpen"
      :lineage-space-id="lineageSpaceId"
      :target-user-id="profileNode.user_id"
      :target-name="profileNode.display.name"
    />

    <!-- 只读关系说明面板（与家族树共用组件）：覆盖层，无任何写操作 -->
    <RelationshipDetailPanel
      v-if="selectedEdge && snapshotMeta !== null"
      :edge="selectedEdge"
      :view-version="snapshotMeta.view_version"
      :computed-at="snapshotMeta.computed_at"
      :resolve-name="resolveName"
      @close="closeRelationDetail"
      @request-correction="goNotifications"
      @view-todos="goNotifications"
    />
  </main>
</template>

<style scoped>
.person-profile-view {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 14px;
  /* 容器几何与家庭首页 household-card-view（1320px / 44px 边距）保持一致 */
  max-width: 1320px;
  margin: 0 auto;
  padding: 32px 44px 48px;
  box-sizing: border-box;
}

/* 09-06 视觉补齐：与家庭首页 family-space-hero 同套大卡设计语言 */
.profile-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 36px 40px 24px;
  box-sizing: border-box;
  background:
    linear-gradient(125deg, color-mix(in srgb, var(--fg-ink) 9%, transparent), transparent 54%),
    var(--fg-glass-surface);
  border: 1px solid var(--fg-glass-border);
  border-top-color: color-mix(in srgb, var(--fg-ink) 30%, transparent);
  border-radius: 8px;
  backdrop-filter: blur(28px) saturate(115%);
  -webkit-backdrop-filter: blur(28px) saturate(115%);
  box-shadow:
    var(--fg-shadow-raised),
    0 2px 0 color-mix(in srgb, var(--fg-surface) 60%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 10%, transparent);
  transition: transform 350ms ease, box-shadow 350ms ease, border-color 350ms ease;
}

@supports not (backdrop-filter: blur(28px)) {
  .profile-hero { background: var(--fg-surface-raised); }
}

.back-row {
  display: flex;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.back-button {
  min-height: 44px;
}

.loading-spin {
  min-height: 160px;
}

/* 内部分区：大卡内部以分隔线组织（不再各自成卡） */
.status-panel,
.identity-card,
.fields-card,
.relations-card {
  padding: 20px 0 16px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.status-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.status-text {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
  line-height: 1.6;
}

.identity-card {
  display: flex;
  align-items: center;
  gap: 14px;
}

.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  flex-shrink: 0;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: var(--fg-radius-control);
}

[data-theme='modern'] .avatar {
  border-radius: 999px;
}

.identity-main {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.identity-name {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
}

/* 可见性层级：icon + 文字（不只靠颜色） */
.level-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.badge-icon {
  width: 12px;
  height: 12px;
  fill: currentColor;
  flex-shrink: 0;
}

.section-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.field-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
}

.field-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-height: 28px;
}

.field-label {
  flex-shrink: 0;
  width: 64px;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.field-value {
  margin: 0;
  font-size: 13px;
  color: var(--fg-ink);
  line-height: 1.6;
  overflow-wrap: anywhere;
}

/* 关系上下文：只读行按钮（≥44px 点按目标） */
.relation-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  box-sizing: border-box;
  font: inherit;
  text-align: left;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  transition:
    box-shadow 0.2s,
    border-color 0.2s;
}

.relation-row:hover,
.relation-row:focus-visible {
  border-color: var(--fg-accent);
  box-shadow: var(--fg-shadow-raised);
}

.relation-term {
  font-family: var(--fg-font-display);
  font-size: 14px;
  font-weight: 700;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: 999px;
  padding: 1px 10px;
  white-space: nowrap;
}

.relation-class {
  font-size: 13px;
  color: var(--fg-ink);
}

.relation-entry {
  margin-left: auto;
  font-size: 12px;
  color: var(--fg-ink-secondary);
  white-space: nowrap;
}

.relations-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.relations-actions:empty {
  display: none;
}

@media (max-width: 480px) {
  .relation-entry {
    display: none;
  }
}
</style>
