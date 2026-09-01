<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NTag,
} from 'naive-ui'

import MaskedField from '@/components/common/MaskedField.vue'
import InviteMemberDialog from '@/components/member/InviteMemberDialog.vue'
import SpaceCreateDialog from '@/components/member/SpaceCreateDialog.vue'
import { useSpaceContext } from '@/composables/useSpaceContext'
import { useAuthStore } from '@/stores/auth'
import { useHouseholdCardStore } from '@/stores/household'
import { useSpacesStore } from '@/stores/spaces'
import type { HouseholdCardMember } from '@/types/api'

/**
 * 我的家庭 / HouseholdCardView（09-01 design.md §5.1）。
 *
 * 数据边界（红线）：
 * - 右栏成员只消费 household store（`GET /household-card` 服务端投影）；加载失败
 *   或合同未就绪（占位端点 404）时显示「家庭卡服务合同未就绪」状态面板，
 *   绝不回退 members store、/users 或本地拼接；
 * - 左栏本人资料用 auth store 的本人 profile（本人数据，不涉及授权投影）；
 * - 编辑资料 / 隐私与公示设置是跳转入口，不做行内编辑；
 * - 空 household 显示服务端 allowed_actions 允许的创建/邀请入口（既有流程组件，
 *   不静默创建）；退出按钮经 useSpaceContext 切换到 lineage 空间，无可用 lineage
 *   时停在家庭卡并显示安全提示。
 */
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const household = useHouseholdCardStore()
const spaceContext = useSpaceContext()

const viewMode = ref<'grid' | 'list'>('grid')
const inviteOpen = ref(false)
const createOpen = ref(false)
/** 无可用 lineage 时点「退出」的安全提示（保留在家庭卡） */
const noLineageHint = ref(false)

const spaceId = computed(() => spaces.currentSpaceId)
const isHouseholdContext = computed(() => spaces.currentSpace?.kind === 'household')
const card = computed(() => (spaceId.value === null ? null : household.forSpace(spaceId.value)))
const loading = computed(() => spaceId.value !== null && household.isLoading(spaceId.value))
const loadError = computed(() => (spaceId.value === null ? null : household.errorFor(spaceId.value)))

/** 卡片标题：服务端投影名优先；加载中回退当前空间名（同为服务端数据） */
const householdName = computed(
  () => card.value?.space_name ?? spaces.currentSpace?.name ?? '我的家庭',
)

const members = computed<HouseholdCardMember[]>(() => card.value?.members ?? [])
const memberCount = computed(() => members.value.length)

const householdSpaces = computed(() => spaces.spaces.filter((space) => space.kind === 'household'))
const lineageSpaces = computed(() => spaces.spaces.filter((space) => space.kind === 'lineage'))

async function loadCard(): Promise<void> {
  if (spaceId.value === null || !isHouseholdContext.value) return
  await household.load(spaceId.value).catch(() => undefined)
}

function retry(): void {
  void household.refresh(spaceId.value ?? 0).catch(() => undefined)
}

onMounted(() => {
  void loadCard()
})

watch(spaceId, () => {
  // 切换空间后按新上下文重新读取投影（304/ETag 由 store 承担）
  noLineageHint.value = false
  void loadCard()
})

/** 退出 → 家族树：切换到最近/第一个可用 lineage；没有可用 lineage 时安全提示 */
async function exitToFamilyTree(): Promise<void> {
  const target = lineageSpaces.value[0]
  if (!target) {
    noLineageHint.value = true
    return
  }
  await spaceContext.switchSpace(target.id)
}

function goToSettings(): void {
  void router.push({ name: 'settings' })
}

function goToNotifications(): void {
  void router.push({ name: 'notifications' })
}

function openMember(member: HouseholdCardMember): void {
  // 点击自己的节点不进入公示页（design.md §2）：本人资料已在左栏
  if (member.user_id === auth.user?.id) return
  void router.push({ name: 'person-profile', params: { userId: String(member.user_id) } })
}

function genderText(value: HouseholdCardMember['display']['gender']): string | null {
  if (typeof value !== 'string') return null
  return value === 'f' ? '女' : value === 'm' ? '男' : '不详'
}

const selfStatus = computed(() =>
  auth.user?.profile_status === 'identity_confirmed' ? '已确档' : '待确档',
)
</script>

<template>
  <main class="household-card-view" data-test="household-card-view">
    <!-- 左上角固定退出按钮：返回当前上下文关联的家族树 -->
    <div class="exit-row">
      <NButton
        quaternary
        size="small"
        class="exit-button"
        data-test="exit-to-family-tree"
        @click="exitToFamilyTree"
      >
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 5H8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h6" />
          <path d="m13 8 4 4-4 4M17 12H9" />
        </svg>
        退出到家族树
      </NButton>
    </div>

    <NAlert
      v-if="noLineageHint"
      type="info"
      :show-icon="true"
      class="context-alert"
      data-test="no-lineage-hint"
    >
      当前没有可用的家族空间，已保留在家庭卡。可以先在家庭空间邀请家人，或创建一个家族空间后再进入家族树。
    </NAlert>

    <!-- 当前空间不是 household：安全上下文提示，不渲染任何家庭卡内容 -->
    <section
      v-if="!isHouseholdContext"
      class="context-panel"
      data-test="household-context-panel"
    >
      <h1 class="card-title">我的家庭</h1>
      <NAlert type="info" :show-icon="true">
        家族树页面正在展示当前家族空间；请先选择一个家庭空间查看家庭卡。
      </NAlert>
      <div v-if="householdSpaces.length > 0" class="context-switch">
        <NButton
          v-for="space in householdSpaces"
          :key="space.id"
          size="small"
          secondary
          :data-test="`switch-to-household-${space.id}`"
          @click="spaceContext.switchSpace(space.id)"
        >
          进入「{{ space.name }}」
        </NButton>
      </div>
      <NAlert v-else type="info" :show-icon="true" data-test="no-household-hint">
        你还没有家庭空间。可以创建一个家庭空间，再邀请家人加入。
        <NButton size="small" type="primary" class="inline-action" @click="createOpen = true">
          创建家庭空间
        </NButton>
      </NAlert>
    </section>

    <template v-else>
      <header class="card-head">
        <h1 class="card-title" data-test="household-name">{{ householdName }}</h1>
        <NTag
          v-if="spaces.isSpaceAdmin"
          size="small"
          type="primary"
          :bordered="true"
          data-test="household-admin-badge"
        >
          空间管理员
        </NTag>
      </header>

      <!-- 合同未就绪 / 加载失败：明确状态面板，无任何成员数据兜底 -->
      <section
        v-if="loadError !== null"
        class="status-panel"
        data-test="contract-not-ready"
      >
        <h2 class="status-title">家庭卡服务合同未就绪</h2>
        <p class="status-text">
          家庭卡投影服务暂时不可用（服务端合同尚未落地或请求失败），已按安全策略不展示任何成员数据。
        </p>
        <NButton size="small" data-test="contract-retry" @click="retry">重新加载</NButton>
      </section>

      <NSpin v-else-if="loading && card === null" class="loading-spin" :show="true" />

      <template v-else-if="card !== null">
        <div class="card-columns">
          <!-- 左栏：本人资料（auth store 本人数据） -->
          <section class="self-card" data-test="self-profile">
            <div class="self-head">
              <span class="avatar" aria-hidden="true">{{ (auth.user?.name ?? '我').slice(0, 1) }}</span>
              <div class="self-main">
                <span class="self-name" data-test="self-name">{{ auth.user?.name ?? '我' }}</span>
                <span
                  class="fg-badge"
                  :class="auth.user?.profile_status === 'identity_confirmed' ? 'fg-badge--confirmed' : 'fg-badge--provisional'"
                  data-test="self-status-badge"
                >
                  {{ selfStatus }}
                </span>
              </div>
            </div>
            <div class="self-actions">
              <NButton size="small" secondary data-test="go-edit-profile" @click="goToSettings">
                编辑资料
              </NButton>
              <NButton size="small" secondary data-test="go-disclosure-settings" @click="goToSettings">
                隐私与公示设置
              </NButton>
            </div>
          </section>

          <!-- 右栏：家庭成员（只来自 household 投影） -->
          <section class="members-card" data-test="household-members">
            <div class="members-head">
              <h2 class="section-title">家庭成员</h2>
              <NRadioGroup
                v-model:value="viewMode"
                size="small"
                name="member-view-mode"
                data-test="member-view-toggle"
                aria-label="成员展示方式"
              >
                <NRadioButton value="grid">网格</NRadioButton>
                <NRadioButton value="list">列表</NRadioButton>
              </NRadioGroup>
            </div>

            <!-- 空 household：保留卡片骨架 + 服务端允许的创建/邀请入口 -->
            <div v-if="memberCount === 0" class="members-empty" data-test="household-empty">
              <p class="empty-hint" data-test="empty-hint">
                {{ card.allowed_actions.empty_state_hint ?? '这个家庭还没有确认的成员。' }}
              </p>
              <div class="empty-actions">
                <NButton
                  v-if="card.allowed_actions.can_invite_members"
                  type="primary"
                  size="small"
                  data-test="invite-entry"
                  @click="inviteOpen = true"
                >
                  邀请家人
                </NButton>
                <NButton
                  v-if="card.allowed_actions.can_create_household"
                  size="small"
                  secondary
                  data-test="create-household-entry"
                  @click="createOpen = true"
                >
                  创建家庭空间
                </NButton>
              </div>
            </div>

            <!-- 网格：confirmed household 成员小卡片 -->
            <div v-else-if="viewMode === 'grid'" class="member-grid" data-test="member-grid">
              <button
                v-for="member in members"
                :key="member.user_id"
                type="button"
                class="member-card"
                :class="{ 'is-self': member.user_id === auth.user?.id }"
                :data-test="`member-card-${member.user_id}`"
                @click="openMember(member)"
              >
                <span class="avatar avatar--small" aria-hidden="true">
                  {{ member.display.name.slice(0, 1) }}
                </span>
                <span class="member-name">{{ member.display.name }}</span>
                <span v-if="member.user_id === auth.user?.id" class="fg-badge fg-badge--accent">我</span>
                <span class="member-label" data-test="household-label">{{ member.household_label }}</span>
                <span class="member-meta">
                  <template v-if="genderText(member.display.gender)">
                    {{ genderText(member.display.gender) }}
                  </template>
                  <MaskedField v-else :value="member.display.gender" />
                  <template v-if="member.display.birth !== null && !('__masked__' in member.display.birth)">
                    · 生 {{ member.display.birth.date ?? '不详' }}
                  </template>
                  <template v-else-if="member.display.birth !== null">
                    · 生 <MaskedField :value="member.display.birth" />
                  </template>
                </span>
                <span class="fg-badge" :class="member.visibility_level === 'lineage_summary' ? 'fg-badge--provisional' : 'fg-badge--neutral'" data-test="member-visibility">
                  {{ member.visibility_level === 'lineage_summary' ? '族谱摘要' : member.visibility_level === 'self_private' ? '仅本人' : '家庭可见' }}
                </span>
              </button>
            </div>

            <!-- 列表：同一投影的纵向排布 -->
            <ul v-else class="member-list" data-test="member-list">
              <li
                v-for="member in members"
                :key="member.user_id"
                class="member-row"
                :data-test="`member-row-${member.user_id}`"
                @click="openMember(member)"
              >
                <span class="avatar avatar--small" aria-hidden="true">
                  {{ member.display.name.slice(0, 1) }}
                </span>
                <span class="member-name">{{ member.display.name }}</span>
                <span v-if="member.user_id === auth.user?.id" class="fg-badge fg-badge--accent">我</span>
                <span class="member-label" data-test="household-label">{{ member.household_label }}</span>
                <span class="member-meta">
                  <template v-if="genderText(member.display.gender)">
                    {{ genderText(member.display.gender) }}
                  </template>
                  <MaskedField v-else :value="member.display.gender" />
                  <template v-if="member.display.birth !== null && !('__masked__' in member.display.birth)">
                    · 生 {{ member.display.birth.date ?? '不详' }}
                  </template>
                  <template v-else-if="member.display.birth !== null">
                    · 生 <MaskedField :value="member.display.birth" />
                  </template>
                </span>
              </li>
            </ul>
          </section>
        </div>

        <!-- 家庭状态区：成员数、待办入口、进入家族树 -->
        <section class="family-status" data-test="family-status">
          <div class="status-item">
            <span class="status-label">家庭成员</span>
            <strong data-test="member-count">{{ memberCount }}</strong>
          </div>
          <div class="status-item">
            <span class="status-label">数据版本</span>
            <strong data-test="household-version">v{{ card.view_version }}</strong>
          </div>
          <div class="status-actions">
            <NButton size="small" secondary data-test="go-notifications" @click="goToNotifications">
              待办与通知
            </NButton>
            <NButton size="small" secondary data-test="go-family-tree" @click="exitToFamilyTree">
              进入家族树
            </NButton>
          </div>
        </section>
      </template>
    </template>

    <!-- 既有领域流程弹窗：仅由显式入口打开（空 household 创建/邀请），不静默创建 -->
    <InviteMemberDialog :visible="inviteOpen" @update:visible="inviteOpen = $event" />
    <SpaceCreateDialog
      :visible="createOpen"
      default-kind="household"
      @update:visible="createOpen = $event"
    />
  </main>
</template>

<style scoped>
.household-card-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 16px 40px;
  box-sizing: border-box;
}

.exit-row {
  display: flex;
}

.exit-button {
  /* 主要导航动作：44px 点按目标（Phase 7 门禁） */
  min-height: 44px;
}

.context-alert {
  width: fit-content;
  max-width: 100%;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.card-title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
}

.context-panel {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 12px;
}

.context-switch {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.inline-action {
  margin-left: 8px;
}

.status-panel,
.self-card,
.members-card {
  padding: 16px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
}

.status-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.status-text {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
  line-height: 1.6;
}

.loading-spin {
  min-height: 160px;
}

.card-columns {
  display: grid;
  grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

/* 左栏：本人资料 */
.self-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.self-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.self-main {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.self-name {
  font-family: var(--fg-font-display);
  font-size: 17px;
  font-weight: 700;
  color: var(--fg-ink);
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

.avatar--small {
  width: 40px;
  height: 40px;
  font-size: 18px;
}

[data-theme='modern'] .avatar {
  border-radius: 999px;
}

.self-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: stretch;
}

/* 右栏：家庭成员 */
.members-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.members-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.section-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.members-empty {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  border: 1px dashed var(--fg-line-strong);
  border-radius: var(--fg-radius-control);
}

.empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.empty-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

/* 成员网格：小卡片 */
.member-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  max-height: 460px;
  overflow-y: auto;
  padding-right: 2px;
}

.member-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 12px;
  font: inherit;
  text-align: left;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  min-height: 44px;
  transition:
    box-shadow 0.2s,
    border-color 0.2s;
}

.member-card:hover {
  border-color: var(--fg-accent);
  box-shadow: var(--fg-shadow-raised);
}

.member-card.is-self {
  border-color: var(--fg-accent);
}

/* 成员列表 */
.member-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 460px;
  overflow-y: auto;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
}

.member-row:hover {
  border-color: var(--fg-accent);
}

.member-name {
  font-family: var(--fg-font-display);
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.member-label {
  padding: 1px 8px;
  font-size: 12px;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: 999px;
  white-space: nowrap;
}

.member-meta {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  color: var(--fg-ink-secondary);
  flex-wrap: wrap;
}

/* 家庭状态区 */
.family-status {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 14px 16px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
}

.status-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 72px;
}

.status-label {
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.status-item strong {
  font-size: 18px;
  color: var(--fg-ink);
}

.status-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
  flex-wrap: wrap;
}

/* 移动端：上下堆叠（本人资料在上、成员区在下，DOM 顺序即堆叠顺序）；
   375px 下成员网格 auto-fill 收敛为单列，可纵向滚动，无横向滚动 */
@media (max-width: 768px) {
  .card-columns {
    grid-template-columns: 1fr;
  }

  .status-actions {
    margin-left: 0;
  }

  /* 紧凑按钮/视图切换在移动端补足 44px 点按目标（抽查门禁） */
  .household-card-view :deep(.n-button--small-type),
  .household-card-view :deep(.n-radio-button) {
    min-height: 44px;
  }
}
</style>
