<script setup lang="ts">
// 统一家庭用户应用壳（09-01 design.md §3.1）：
// - 桌面端左侧固定导航：一级入口 = 我的家庭 / 家族树 / 记忆与知识 / 统计，
//   次级 = 设置；「空间管理」仅当前空间 space_admin 权限成立时出现；
// - 顶部：通知入口、主题切换、账号菜单；Assistant 由根组件提供；
// - 空间选择器按 household/lineage 分组，切换触发 useSpaceContext 空间切换事务；
// - 背景贯穿应用壳；家庭与家族视图提供可降级的 Three.js 景深星点。
// 旧全局搜索（GlobalSearch，走旧 /search 合同）已从壳移除：新壳导航不含全局搜索，
// 旧全局搜索与授权边界冲突，后续按空间内检索另行设计（记录见任务 notes.md）。
import { NPopover, NSelect, NSwitch, type SelectOption } from 'naive-ui'
import { computed, h, ref, watch, type VNodeChild } from 'vue'
import { Bell, BookOpen, ChartNoAxesColumn, ChevronRight, House, Network, Settings, ShieldCheck, UserRound } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import { useSpacesStore } from '@/stores/spaces'
import { useUiStore } from '@/stores/ui'
import { useSpaceContext } from '@/composables/useSpaceContext'
import CosmicBackdrop from '@/components/canvas/CosmicBackdrop.vue'

const SPACE_KIND_GROUP_LABELS = { household: '家庭空间', lineage: '家族空间' } as const

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const ui = useUiStore()
const notifications = useNotificationsStore()
const spaceContext = useSpaceContext()

const switching = ref(false)
const navEntries = [
  { name: 'home', label: '我的家庭', short: '我的家庭', icon: House },
  { name: 'family-space', label: '家族树', short: '家族树', icon: Network },
  { name: 'memory', label: '记忆与知识', short: '记忆', icon: BookOpen },
  { name: 'stats', label: '统计', short: '统计', icon: ChartNoAxesColumn },
]
const currentPageLabel = computed(() => navEntries.find((entry) => entry.name === route.name)?.label ?? '家庭空间')
const cosmicView = computed(() => route.name === 'home' || route.name === 'family-space')
const pickerKind = computed<'household' | 'lineage'>(() =>
  spaces.currentSpace?.kind ?? (route.name === 'family-space' ? 'lineage' : 'household'),
)
const pickerCaption = computed(() => pickerKind.value === 'lineage' ? '当前家族空间' : '当前家庭空间')

const canManageCurrentSpace = computed(() => spaces.canManageSpace && spaces.currentSpace !== null)

const spaceManagementTarget = computed(() =>
  spaces.currentSpace
    ? { name: 'space-management', params: { spaceId: spaces.currentSpace.id } }
    : { name: 'family-space' },
)

/**
 * 空间选择器选项（design.md §3.1）：按 household/lineage 分组，
 * 分组标题即类型标记；选项渲染名称 + 当前空间管理员标记。
 * 管理员判定只依据已加载的成员关系投影（spaces store），不做本地猜测。
 */
const spacePickerOptions = computed<SelectOption[]>(() => {
  const options: SelectOption[] = []
  for (const kind of [pickerKind.value] as const) {
    const children = spaces.spaces
      .filter((space) => space.kind === kind)
      .map((space) => ({ label: space.name, value: space.id }))
    if (children.length > 0) {
      options.push({ type: 'group', label: SPACE_KIND_GROUP_LABELS[kind], key: kind, children })
    }
  }
  return options
})

function isSpaceAdminOf(spaceId: SelectOption['value']): boolean {
  const userId = auth.user?.id
  if (typeof spaceId !== 'number' || userId === undefined) return false
  return spaces.members.some(
    (member) =>
      member.space_id === spaceId &&
      member.user_id === userId &&
      member.status === 'active' &&
      member.role === 'space_admin',
  )
}

function renderSpaceOptionLabel(option: SelectOption): VNodeChild {
  const name = typeof option.label === 'string' ? option.label : ''
  const children = [
    h('span', { class: 'space-option-name' }, name),
    isSpaceAdminOf(option.value)
      ? h('span', { class: 'space-option-badge' }, '管理员')
      : null,
  ]
  return h('span', { class: 'space-option' }, children)
}

async function onSpaceSelect(value: string | number | null): Promise<void> {
  if (typeof value !== 'number' || value === spaces.currentSpaceId) return
  switching.value = true
  try {
    // 空间切换事务：校验 → epoch/context → 清旧缓存 → 载新投影 → 重算目标并导航
    await spaceContext.switchSpace(value)
  } finally {
    switching.value = false
  }
}

// 通知未读数：来自 notifications store 的服务端载荷。
// 通知端点运行时 404（BLOCKER 占位）→ load 失败被安静吞掉：无角标、无错误横幅。
const unreadCount = computed(() => {
  const spaceId = spaces.currentSpaceId
  return spaceId === null ? 0 : notifications.unreadCountOf(spaceId)
})

watch(
  () => spaces.currentSpaceId,
  (spaceId) => {
    if (spaceId === null) return
    void notifications.load(spaceId).catch(() => undefined)
  },
  { immediate: true },
)

// 会话默认空间：登录 / 硬刷新后按优先级选择（最近 household > own/managed
// household > 第一个 household > 第一个 lineage）；完全没有空间时由 Phase 3
// 家庭卡的创建/邀请空状态承载引导，这里不静默创建。
// 以 watch(isLoggedIn) 而非 onMounted 触发：App.vue 在 router 初始导航未解析
// 时 route.meta 为空，壳会先于登录页短暂挂载——onMounted 时认证尚未恢复
// （硬刷新场景），且未登录时发起 spaces.load 必然 401，叠加会话过期跳转
// 曾形成 /login 无限重载循环（09-01 走查实测）。
watch(
  () => auth.isLoggedIn,
  (loggedIn) => {
    if (!loggedIn) return
    void spaceContext
      .ensureDefaultSpace({ defaultRouteFallback: true })
      .catch(() => undefined)
  },
  { immediate: true },
)

function onThemeSwitch(value: boolean): void {
  ui.setTheme(value ? 'modern' : 'paper')
}

async function syncRouteSpace(routeName: string | symbol | null | undefined): Promise<void> {
  const targetKind = routeName === 'family-space' ? 'lineage' : routeName === 'home' ? 'household' : null
  if (targetKind === null || spaces.currentSpace?.kind === targetKind) return
  const preferred = targetKind === 'household' && ui.recentHouseholdId !== null
    ? spaces.spaces.find((space) => space.id === ui.recentHouseholdId && space.kind === targetKind)
    : undefined
  const target = preferred ?? spaces.spaces.find((space) => space.kind === targetKind)
  if (target) await spaceContext.switchSpace(target.id)
}

watch(
  [() => route.name, () => spaces.spaces.length, () => spaces.currentSpace?.kind],
  ([routeName]) => { void syncRouteSpace(routeName) },
  { immediate: true },
)

function isNavActive(name: string): boolean {
  return route.name === name
}

async function goSettings(): Promise<void> {
  await router.push({ name: 'settings' })
}

async function goSpaceManagement(): Promise<void> {
  await router.push(spaceManagementTarget.value)
}

/** 登出：auth.logout 全量清理敏感缓存与凭据后回登录页 */
async function handleLogout(): Promise<void> {
  await auth.logout()
  await router.replace({ name: 'login' })
}

// 测试锚点：选项分组结构 + 切换事务接线（useSpaceContext 本身单独测试）
defineExpose({ spacePickerOptions, onSpaceSelect })
</script>

<template>
  <div class="app-shell" :class="{ 'app-shell--cosmic': cosmicView }">
    <aside class="shell-sidebar">
      <RouterLink class="shell-brand" :to="{ name: 'home' }" aria-label="FamilyGraph 我的家庭">
        <Network class="brand-mark" :size="24" aria-hidden="true" />
        <span>FamilyGraph</span>
      </RouterLink>
      <div class="sidebar-picker">
        <span class="sidebar-caption">{{ pickerCaption }}</span>
        <NSelect class="space-select" :value="spaces.currentSpaceId" :options="spacePickerOptions"
          :render-label="renderSpaceOptionLabel" :loading="switching" placeholder="选择空间"
          size="medium" :consistent-menu-width="false" aria-label="切换当前空间"
          data-test="space-picker" @update:value="onSpaceSelect" />
      </div>
      <nav class="sidebar-nav" aria-label="主导航">
        <RouterLink v-for="entry in navEntries" :key="entry.name" class="nav-link"
          :to="{ name: entry.name }" :class="{ 'nav-link--active': isNavActive(entry.name) }"
          :aria-current="isNavActive(entry.name) ? 'page' : undefined">
          <component :is="entry.icon" :size="19" aria-hidden="true" />
          <span>{{ entry.label }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-divider" role="presentation"></div>
      <nav class="sidebar-nav sidebar-nav--secondary" aria-label="次级导航">
        <RouterLink class="nav-link" :to="{ name: 'settings' }"
          :class="{ 'nav-link--active': isNavActive('settings') }"
          :aria-current="isNavActive('settings') ? 'page' : undefined">
          <Settings :size="19" aria-hidden="true" /><span>设置</span>
        </RouterLink>
        <RouterLink v-if="canManageCurrentSpace" class="nav-link" :to="spaceManagementTarget"
          :class="{ 'nav-link--active': isNavActive('space-management') }"
          :aria-current="isNavActive('space-management') ? 'page' : undefined"
          data-test="space-management-link">
          <ShieldCheck :size="19" aria-hidden="true" /><span>空间管理</span>
        </RouterLink>
      </nav>
      <div class="sidebar-account">
        <UserRound :size="20" aria-hidden="true" />
        <div><span class="sidebar-caption">当前账户</span><strong>{{ auth.user?.name ?? '已登录' }}</strong></div>
      </div>
    </aside>
    <div class="shell-body">
      <header class="shell-topbar">
        <RouterLink class="shell-brand shell-brand--topbar" :to="{ name: 'home' }" aria-label="FamilyGraph 我的家庭">
          <Network :size="21" aria-hidden="true" /><span>FamilyGraph</span>
        </RouterLink>
        <div class="topbar-location">
          <span>{{ currentPageLabel }}</span><ChevronRight :size="14" aria-hidden="true" />
          <strong>{{ spaces.currentSpace?.name ?? '我的空间' }}</strong>
        </div>
        <div class="topbar-actions">
          <NSwitch class="theme-switch" :value="ui.theme === 'modern'"
            aria-label="切换配色主题（暮色 / 雾青）" @update:value="onThemeSwitch">
            <template #checked>雾青</template><template #unchecked>暮色</template>
          </NSwitch>
          <RouterLink class="topbar-button" :to="{ name: 'notifications' }"
            aria-label="通知" title="通知" data-test="notifications-entry">
            <Bell :size="19" aria-hidden="true" />
            <span v-if="unreadCount > 0" class="unread-badge" data-test="notifications-unread">
              {{ unreadCount > 99 ? '99+' : unreadCount }}
            </span>
          </RouterLink>
          <NPopover trigger="click" placement="bottom-end">
            <template #trigger>
              <button class="topbar-button account-trigger" type="button" aria-label="账号菜单"
                title="账号菜单" data-test="account-menu-trigger">
                <UserRound :size="19" aria-hidden="true" />
              </button>
            </template>
            <div class="account-menu" role="menu" aria-label="账号菜单">
              <p class="account-name" data-test="account-menu-name">{{ auth.user?.name ?? '已登录' }}</p>
              <button class="account-item" type="button" role="menuitem"
                data-test="account-menu-settings" @click="goSettings">设置</button>
              <button v-if="canManageCurrentSpace" class="account-item" type="button" role="menuitem"
                data-test="account-menu-space-management" @click="goSpaceManagement">空间管理</button>
              <button class="account-item account-item--danger" type="button" role="menuitem"
                data-test="account-menu-logout" @click="handleLogout">登出</button>
            </div>
          </NPopover>
        </div>
      </header>
      <main class="shell-main" :class="{ 'shell-main--cosmic': cosmicView }">
        <CosmicBackdrop v-if="cosmicView" />
        <RouterView />
      </main>
      <nav class="shell-bottom-nav" aria-label="底部导航">
        <RouterLink v-for="entry in navEntries" :key="entry.name" class="bottom-link"
          :to="{ name: entry.name }" :class="{ 'bottom-link--active': isNavActive(entry.name) }"
          :aria-current="isNavActive(entry.name) ? 'page' : undefined">
          <component :is="entry.icon" :size="21" aria-hidden="true" /><span>{{ entry.short }}</span>
        </RouterLink>
      </nav>
    </div>
  </div>
</template>

<style scoped>
.app-shell { position: relative; isolation: isolate; display: flex; min-height: 100vh; min-width: 0; background: linear-gradient(120deg, color-mix(in srgb, var(--fg-ink) 8%, transparent), transparent 58%), var(--fg-surface); }
.app-shell::before { content: ''; position: fixed; inset: 0; z-index: -1; pointer-events: none; background: radial-gradient(ellipse 75% 60% at 12% 10%, color-mix(in srgb, var(--fg-accent) 10%, transparent), transparent 68%); opacity: 0.75; }
.app-shell--cosmic::before { background-image: url('/images/starfield.jpg'); background-size: cover; background-position: center; opacity: 0.045; }
.shell-sidebar {
  position: sticky; top: 0; height: 100dvh; display: flex; flex-direction: column;
  gap: 24px; width: 224px; flex: 0 0 224px; box-sizing: border-box; padding: 22px 16px;
  background: color-mix(in srgb, var(--fg-surface) 70%, transparent); border-right: 1px solid var(--fg-glass-border);
  backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
}
.shell-brand {
  display: inline-flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 6px;
  font-family: var(--fg-font-body); font-size: 19px; font-weight: 650;
  letter-spacing: 0; color: var(--fg-ink); text-decoration: none; white-space: nowrap;
}
.brand-mark { color: var(--fg-accent); flex: 0 0 auto; }
.sidebar-caption { display: block; font-size: 11px; font-weight: 500; color: var(--fg-ink-secondary); margin-bottom: 8px; }
.sidebar-picker { min-width: 0; padding: 0 6px; }
.sidebar-picker :deep(.space-select) { width: 100%; }
.sidebar-nav { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.sidebar-divider { height: 1px; margin: 0 8px; background: var(--fg-line); }
.nav-link {
  display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 8px 12px;
  box-sizing: border-box; border-radius: 6px; color: var(--fg-ink-secondary);
  font-size: 13px; text-decoration: none; white-space: nowrap;
}
.nav-link:hover { color: var(--fg-ink); background: var(--fg-surface-sunken); }
.nav-link--active { color: var(--fg-accent); background: var(--fg-accent-soft); font-weight: 650; }
.sidebar-account { margin-top: auto; border-top: 1px solid var(--fg-line); padding: 20px 8px 0; display: flex; gap: 12px; align-items: center; }
.sidebar-account strong { display: block; font-size: 13px; overflow-wrap: anywhere; }
.sidebar-account .sidebar-caption { margin-bottom: 3px; }
.shell-body { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.shell-topbar {
  position: sticky; top: 0; z-index: 100; display: flex; align-items: center; gap: 16px;
  min-height: 72px; padding: 10px 32px; box-sizing: border-box;
  background: var(--fg-glass-surface-raised); backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px); border-bottom: 1px solid var(--fg-glass-border);
}
.shell-brand--topbar { display: none; padding: 0; font-size: 16px; gap: 6px; }
.topbar-location { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--fg-ink-secondary); min-width: 0; }
.topbar-location strong { font-weight: 500; color: var(--fg-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topbar-picker { display: none; }
.topbar-actions { display: flex; align-items: center; gap: 10px; min-width: 0; margin-left: auto; }
.topbar-button {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; flex: 0 0 auto; padding: 0; border: none; border-radius: 50%;
  background: transparent; color: var(--fg-ink-secondary); cursor: pointer; box-sizing: border-box;
}
.topbar-button:hover { background: var(--fg-surface-sunken); color: var(--fg-ink); }
.account-trigger { background: var(--fg-surface-sunken); border: 1px solid var(--fg-line); }
.unread-badge {
  position: absolute; top: 1px; right: -1px; display: inline-flex; align-items: center;
  justify-content: center; min-width: 18px; height: 18px; padding: 0 4px; box-sizing: border-box;
  border-radius: 9px; background: var(--fg-accent); color: var(--fg-accent-ink); font-size: 10px; font-weight: 700;
}
.theme-switch { flex-shrink: 0; margin-right: 6px; }
.account-menu { display: flex; flex-direction: column; min-width: 160px; }
.account-name { margin: 0; padding: 8px 12px; font-size: 13px; font-weight: 600; color: var(--fg-ink); border-bottom: 1px solid var(--fg-line); }
.account-item { display: flex; align-items: center; min-height: 44px; padding: 8px 12px; border: none; background: none; color: var(--fg-ink-secondary); font: inherit; text-align: left; cursor: pointer; }
.account-item:hover { color: var(--fg-ink); background: var(--fg-surface-sunken); }
.account-item--danger { color: var(--fg-status-disputed); }
.shell-main { position: relative; min-width: 0; flex: 1; background: transparent; }
.shell-main--cosmic { isolation: isolate; }
.shell-bottom-nav {
  display: none; position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
  align-items: stretch; justify-content: space-around; background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px); border-top: 1px solid var(--fg-line);
}
.bottom-link { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-width: 64px; min-height: 52px; padding: 6px 8px; color: var(--fg-ink-secondary); font-size: 11px; text-decoration: none; }
.bottom-link--active { color: var(--fg-accent); font-weight: 600; }
.sidebar-picker :deep(.space-option), .topbar-picker :deep(.space-option) { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.sidebar-picker :deep(.space-option-name), .topbar-picker :deep(.space-option-name) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-picker :deep(.space-option-badge), .topbar-picker :deep(.space-option-badge) { flex: 0 0 auto; padding: 1px 6px; border-radius: 4px; background: var(--fg-accent-soft); color: var(--fg-accent); font-size: 11px; font-weight: 600; }
@supports not (backdrop-filter: blur(12px)) {
  .shell-topbar, .shell-bottom-nav, .shell-sidebar { background: var(--fg-surface-raised); }
}
@media (max-width: 768px) {
  .shell-sidebar { display: none; }
  .shell-brand--topbar { display: inline-flex; flex: 0 0 auto; }
  .topbar-location { display: none; }
  .shell-topbar { flex-wrap: wrap; gap: 4px; row-gap: 8px; padding: 10px 12px; }
  .topbar-actions { gap: 2px; }
  .theme-switch { margin-right: 2px; }
  .shell-main { padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px)); }
  .shell-bottom-nav { display: flex; padding-bottom: env(safe-area-inset-bottom, 0px); }
}
</style>
