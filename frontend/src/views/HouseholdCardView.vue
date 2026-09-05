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
    <!-- 左上角固定退出按钮：返回当前上下文关联的家族树（水晶圆形 FAB，PRD §2.3） -->
    <div class="exit-row">
      <button
        type="button"
        class="fg-fab-btn fg-fab-btn--accent exit-button"
        data-test="exit-to-family-tree"
        aria-label="退出到家族树"
        title="退出到家族树"
        @click="exitToFamilyTree"
      >
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M14 5H8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h6" />
          <path d="m13 8 4 4-4 4M17 12H9" />
        </svg>
      </button>
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

        <!-- 家庭状态区：成员数与数据版本（操作收敛至底部圆形 FAB Dock，PRD §2.2） -->
        <section class="family-status" data-test="family-status">
          <div class="status-item">
            <span class="status-label">家庭成员</span>
            <strong data-test="member-count">{{ memberCount }}</strong>
          </div>
          <div class="status-item">
            <span class="status-label">数据版本</span>
            <strong data-test="household-version">v{{ card.view_version }}</strong>
          </div>
        </section>

        <!-- 底部居中悬浮圆形操作 Dock：主要功能操作收敛入口（PRD §2.2） -->
        <nav class="fab-dock" data-test="household-fab-dock" aria-label="家庭卡操作">
          <button
            type="button"
            class="fg-fab-btn"
            data-test="go-edit-profile"
            aria-label="编辑资料"
            title="编辑资料"
            @click="goToSettings"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z" />
            </svg>
          </button>
          <button
            type="button"
            class="fg-fab-btn"
            data-test="go-notifications"
            aria-label="待办与通知"
            title="待办与通知"
            @click="goToNotifications"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
          </button>
          <button
            type="button"
            class="fg-fab-btn fg-fab-btn--accent"
            data-test="go-family-tree"
            aria-label="进入家族树"
            title="进入家族树"
            @click="exitToFamilyTree"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <circle cx="12" cy="5" r="2.2" />
              <circle cx="5" cy="19" r="2.2" />
              <circle cx="19" cy="19" r="2.2" />
              <path d="M12 7.2v4.3M6.7 17.2 10.4 12M17.3 17.2 13.6 12" />
            </svg>
          </button>
        </nav>
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
  /* 底部为悬浮 FAB Dock 预留空间，避免遮挡家庭状态区 */
  padding: 20px 16px 96px;
  box-sizing: border-box;
}

.exit-row {
  display: flex;
}

/* 退出至家族树：醒目水晶圆形返回按钮（PRD §2.3）；min-height 契约 = 44px 点按目标 */
.exit-button {
  min-height: 44px;
  box-shadow:
    0 4px 16px var(--fg-glass-glow),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 35%, transparent),
    inset 0 1px 0 0 color-mix(in srgb, var(--fg-surface-raised) 35%, transparent);
}

/* 底部居中悬浮圆形操作 Dock：胶囊毛玻璃 + 圆形按钮群（PRD §2.2） */
.fab-dock {
  position: fixed;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: 999px;
  box-shadow:
    0 8px 32px 0 color-mix(in srgb, var(--fg-ink) 12%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 8%, transparent),
    inset 0 1px 0 0 color-mix(in srgb, var(--fg-surface-raised) 25%, transparent);
  /* 自绘悬浮件层级：高于壳导航（100），低于 naive 浮层（≥2000）与悬浮入口（1500） */
  z-index: 900;
  max-width: calc(100vw - 32px);
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
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.02em;
  background: linear-gradient(135deg, var(--fg-accent) 0%, color-mix(in srgb, var(--fg-accent) 70%, var(--fg-ink) 30%) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  animation: titleSlideIn 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes titleSlideIn {
  from {
    opacity: 0;
    transform: translateX(-16px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
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
  padding: 24px;
  background: var(--fg-glass-surface);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  box-shadow:
    0 8px 32px 0 color-mix(in srgb, var(--fg-ink) 8%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 10%, transparent),
    inset 0 1px 0 0 color-mix(in srgb, var(--fg-surface-raised) 25%, transparent);
  animation: cardSlideIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
}

@keyframes cardSlideIn {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.self-card {
  animation-delay: 0.1s;
  animation-fill-mode: backwards;
}

.members-card {
  animation-delay: 0.2s;
  animation-fill-mode: backwards;
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
  font-size: 18px;
  font-weight: 700;
  color: var(--fg-ink);
  letter-spacing: 0.01em;
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
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 12%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 8%, transparent) 100%
  );
  border: 2px solid color-mix(in srgb, var(--fg-accent) 20%, transparent);
  border-radius: var(--fg-radius-control);
  box-shadow: 0 2px 8px color-mix(in srgb, var(--fg-accent) 15%, transparent);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.member-card:hover .avatar,
.member-row:hover .avatar {
  transform: scale(1.1);
  box-shadow: 0 4px 12px color-mix(in srgb, var(--fg-accent) 25%, transparent);
  border-color: color-mix(in srgb, var(--fg-accent) 35%, transparent);
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
  font-family: var(--fg-font-display);
  font-size: 16px;
  font-weight: 700;
  color: var(--fg-ink);
  letter-spacing: 0.02em;
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

/* 成员网格：小卡片 + 中心聚焦效果 */
.member-grid {
  position: relative;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  max-height: 460px;
  overflow-y: auto;
  padding: 20px;
  padding-right: 22px;
  border-radius: var(--fg-radius-card);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-surface-sunken) 20%, transparent) 0%,
    transparent 100%
  );
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--fg-accent) 25%, transparent) transparent;
}

/* 中心聚焦遮罩：径向渐变，中心清晰，边缘模糊淡出 */
.member-grid::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(
    ellipse 65% 60% at 50% 50%,
    transparent 0%,
    transparent 30%,
    color-mix(in srgb, var(--fg-surface-raised) 20%, transparent) 55%,
    color-mix(in srgb, var(--fg-surface-raised) 40%, transparent) 75%,
    color-mix(in srgb, var(--fg-surface-raised) 60%, transparent) 90%,
    color-mix(in srgb, var(--fg-surface-raised) 75%, transparent) 100%
  );
  z-index: 1;
  border-radius: var(--fg-radius-card);
  transition: opacity 0.4s ease;
}

/* 悬停或聚焦网格时减弱遮罩，让用户看清周围卡片 */
.member-grid:hover::before,
.member-grid:focus-within::before {
  opacity: 0.4;
}

/* 滚动时也减弱遮罩 */
.member-grid:active::before {
  opacity: 0.5;
}

.member-card {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 14px;
  font: inherit;
  text-align: left;
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 97%, var(--fg-accent) 3%) 100%
  );
  border: 1px solid var(--fg-line-strong);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  min-height: 44px;
  transition:
    transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1),
    box-shadow 0.3s cubic-bezier(0.34, 1.56, 0.64, 1),
    border-color 0.3s ease,
    background 0.3s ease;
  animation: cardFadeIn 0.4s ease backwards;
}

@keyframes cardFadeIn {
  from {
    opacity: 0;
    transform: scale(0.95) translateY(8px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}

/* 为每张卡片添加延迟动画，营造依次出现的效果 */
.member-card:nth-child(1) { animation-delay: 0.05s; }
.member-card:nth-child(2) { animation-delay: 0.1s; }
.member-card:nth-child(3) { animation-delay: 0.15s; }
.member-card:nth-child(4) { animation-delay: 0.2s; }
.member-card:nth-child(5) { animation-delay: 0.25s; }
.member-card:nth-child(6) { animation-delay: 0.3s; }
.member-card:nth-child(7) { animation-delay: 0.35s; }
.member-card:nth-child(8) { animation-delay: 0.4s; }

.member-card:hover,
.member-card:focus-visible {
  transform: scale(1.05) translateY(-4px);
  z-index: 10;
  border-color: var(--fg-accent);
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 94%, var(--fg-accent) 6%) 100%
  );
  box-shadow:
    0 0 0 1px var(--fg-accent),
    0 8px 24px color-mix(in srgb, var(--fg-accent) 20%, transparent),
    var(--fg-shadow-raised);
}

.member-card.is-self {
  border-color: var(--fg-accent);
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 92%, var(--fg-accent) 8%) 100%
  );
  box-shadow:
    0 0 0 2px var(--fg-accent),
    0 0 16px color-mix(in srgb, var(--fg-accent) 25%, transparent),
    var(--fg-shadow-card);
}

.member-card.is-self:hover {
  transform: scale(1.08) translateY(-6px);
  box-shadow:
    0 0 0 2px var(--fg-accent),
    0 0 24px color-mix(in srgb, var(--fg-accent) 35%, transparent),
    var(--fg-shadow-raised);
}

/* 成员列表 */
.member-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--fg-accent) 25%, transparent) transparent;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 98%, var(--fg-accent) 2%) 100%
  );
  border: 1px solid var(--fg-line-strong);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
  transition:
    transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1),
    box-shadow 0.3s ease,
    border-color 0.3s ease,
    background 0.3s ease;
  animation: rowSlideIn 0.4s ease backwards;
}

@keyframes rowSlideIn {
  from {
    opacity: 0;
    transform: translateX(-12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

.member-row:nth-child(1) { animation-delay: 0.05s; }
.member-row:nth-child(2) { animation-delay: 0.1s; }
.member-row:nth-child(3) { animation-delay: 0.15s; }
.member-row:nth-child(4) { animation-delay: 0.2s; }
.member-row:nth-child(5) { animation-delay: 0.25s; }
.member-row:nth-child(6) { animation-delay: 0.3s; }
.member-row:nth-child(7) { animation-delay: 0.35s; }
.member-row:nth-child(8) { animation-delay: 0.4s; }

.member-row:hover,
.member-row:focus-visible {
  transform: translateX(4px);
  border-color: var(--fg-accent);
  background: linear-gradient(
    135deg,
    var(--fg-surface-raised) 0%,
    color-mix(in srgb, var(--fg-surface-raised) 95%, var(--fg-accent) 5%) 100%
  );
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 50%, transparent),
    var(--fg-shadow-raised);
}

.member-name {
  font-family: var(--fg-font-display);
  font-size: 16px;
  font-weight: 700;
  color: var(--fg-ink);
  letter-spacing: 0.01em;
}

.member-label {
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--fg-accent);
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 10%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 6%, transparent) 100%
  );
  border: 1px solid color-mix(in srgb, var(--fg-accent) 20%, transparent);
  border-radius: 999px;
  white-space: nowrap;
  transition: all 0.3s ease;
}

.member-card:hover .member-label,
.member-row:hover .member-label {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 15%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 10%, transparent) 100%
  );
  border-color: color-mix(in srgb, var(--fg-accent) 30%, transparent);
  transform: translateY(-1px);
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
  gap: 16px;
  flex-wrap: wrap;
  padding: 18px 24px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  box-shadow:
    0 8px 32px 0 color-mix(in srgb, var(--fg-ink) 8%, transparent),
    0 0 0 1px color-mix(in srgb, var(--fg-accent) 12%, transparent),
    inset 0 1px 0 0 color-mix(in srgb, var(--fg-surface-raised) 25%, transparent);
  animation: cardSlideIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
  animation-delay: 0.3s;
  animation-fill-mode: backwards;
}

.status-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 80px;
  padding: 8px 12px;
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 6%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 3%, transparent) 100%
  );
  border-radius: var(--fg-radius-control);
  transition: all 0.3s ease;
}

.status-item:hover {
  background: linear-gradient(
    135deg,
    color-mix(in srgb, var(--fg-accent) 10%, transparent) 0%,
    color-mix(in srgb, var(--fg-accent) 5%, transparent) 100%
  );
  transform: translateY(-2px);
}

.status-label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--fg-ink-secondary);
  opacity: 0.8;
}

.status-item strong {
  font-size: 20px;
  font-weight: 700;
  font-family: var(--fg-font-display);
  color: var(--fg-accent);
}

/* 移动端：上下堆叠（本人资料在上、成员区在下，DOM 顺序即堆叠顺序）；
   375px 下成员网格 auto-fill 收敛为单列，可纵向滚动，无横向滚动 */
@media (max-width: 768px) {
  .card-columns {
    grid-template-columns: 1fr;
  }

  /* 紧凑按钮/视图切换在移动端补足 44px 点按目标（抽查门禁） */
  .household-card-view :deep(.n-button--small-type),
  .household-card-view :deep(.n-radio-button) {
    min-height: 44px;
  }
}
</style>
