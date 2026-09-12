<script setup lang="ts">
// 统一家庭用户应用壳（09-01 design.md §3.1）：
// - 桌面端左侧固定导航：一级入口 = 我的家庭 / 家族树 / 统计，
//   次级 = 记忆与知识、设置；「空间管理」仅当前空间 space_admin 权限成立时出现；
//   移动端底部导航仍是四个一级入口（记忆与知识在窄屏保留一级触达）。
// - 顶部：通知入口、主题切换、账号菜单；Assistant 由根组件提供；
// - 空间选择器只展示家族空间，切换触发 useSpaceContext 空间切换事务；
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

const SPACE_KIND_GROUP_LABELS = { lineage: '家族空间' } as const

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
// 桌面侧栏一级分组不含记忆与知识（09-06 信息架构调整：归入次级分组、置于设置上方）；
// navEntries 仍完整用于顶栏面包屑与移动端底部导航。
const sidebarPrimaryEntries = navEntries.filter((entry) => entry.name !== 'memory')
const currentPageLabel = computed(() => navEntries.find((entry) => entry.name === route.name)?.label ?? '家庭空间')
const cosmicView = computed(() => route.name === 'home' || route.name === 'family-space')
// 家族空间是唯一的切换维度。它在所有页面保持不变；当前页面决定选择后
// 打开对应的家庭卡还是家族树，不再把 household/lineage 暴露成两套选择。
const pickerCaption = '当前家族空间'
const lineageSpaces = computed(() => spaces.lineageSpaces)
type FamilySpaceOption = {
  value: number
  label: string
  lineageId: number | null
  householdId: number | null
  householdIds: number[]
}

/**
 * 一个家族只占一个选择项：lineage 是家族主身份；配对 household 经
 * lineage_space_id（显式配对，spaces store 解析）或 owner 唯一匹配（旧数据
 * 回退）落到家族项。解析不到所属 lineage 的孤立 household 保留为自己的
 * 家族项，保证独立家庭不会从切换器消失。
 */
const familySpaceOptions = computed<FamilySpaceOption[]>(() => {
  const lineageOptions = lineageSpaces.value.map((lineage) => ({
    value: lineage.id,
    label: lineage.name,
    lineageId: lineage.id,
    householdId: spaces.householdForLineage(lineage.id)?.id ?? null,
    householdIds: spaces.spaces
      .filter((space) => space.kind === 'household' && spaces.lineageForSpace(space.id)?.id === lineage.id)
      .map((space) => space.id),
  }))
  const orphanHouseholds = spaces.spaces
    .filter((space) => space.kind === 'household' && spaces.lineageForSpace(space.id) === null)
    .map((space) => ({
      value: space.id,
      label: space.name,
      lineageId: null,
      householdId: space.id,
      householdIds: [space.id],
    }))
  return [...lineageOptions, ...orphanHouseholds]
})

const selectedFamilySpaceId = computed(() => {
  const currentId = spaces.currentSpaceId
  if (currentId === null) return null
  return familySpaceOptions.value.find(
    (option) => option.lineageId === currentId || option.householdIds.includes(currentId),
  )?.value ?? null
})

const canManageCurrentSpace = computed(() => spaces.canManageSpace && spaces.currentSpace !== null)

const spaceManagementTarget = computed(() =>
  spaces.currentSpace
    ? { name: 'space-management', params: { spaceId: spaces.currentSpace.id } }
    : { name: 'family-space' },
)

/**
 * 空间选择器选项（design.md §3.1）：所有页面只显示一组家族空间。
 * 家庭卡与家族树由当前路由决定落点，不把 household/lineage 暴露成两套选项。
 */
const spacePickerOptions = computed<SelectOption[]>(() => {
  return familySpaceOptions.value.length === 0
    ? []
    : [{
        type: 'group',
        label: SPACE_KIND_GROUP_LABELS.lineage,
        key: 'lineage',
        children: familySpaceOptions.value.map((space) => ({ label: space.label, value: space.value })),
      }]
})

function renderSpaceOptionLabel(option: SelectOption): VNodeChild {
  const name = typeof option.label === 'string' ? option.label : ''
  return h('span', { class: 'space-option' }, [h('span', { class: 'space-option-name' }, name)])
}

async function onSpaceSelect(value: string | number | null): Promise<void> {
  if (typeof value !== 'number') return
  const family = familySpaceOptions.value.find((space) => space.value === value)
  if (!family) return
  // 当前页面决定落点：家族树页取该家族的 lineage，家庭页取家庭卡；
  // 设置/统计/记忆等页面保持当前空间类型，不因切换家族而跳回默认页。
  const current = spaces.currentSpace
  const targetId = route.name === 'family-space'
    ? family.lineageId ?? family.householdId
    : route.name === 'home'
      ? family.householdId ?? family.lineageId
      : current?.kind === 'lineage'
        ? family.lineageId ?? family.householdId
        : current?.kind === 'household'
          ? family.householdId ?? family.lineageId
          : family.householdId ?? family.lineageId
  if (targetId === null) return
  const target = spaces.spaces.find((space) => space.id === targetId)
  if (!target) return
  if (target.id === spaces.currentSpaceId) {
    // 已经在目标家族，选择器只需保持当前值，不再重复导航或刷新。
    return
  }
  switching.value = true
  try {
    // 空间切换事务：校验 → epoch/context → 清旧缓存 → 载新投影 → 重算目标并导航；
    // 家庭/家族页保持本页语义，其他页面（统计/记忆/设置）只换上下文不跳页
    const onSpacePage = route.name === 'home' || route.name === 'family-space'
    await spaceContext.switchSpace(targetId, { navigate: onSpacePage })
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

/** 一级页面导航只切换当前家族的视图，不重新选择或跳到其他家族。 */
async function syncRouteSpace(routeName: string | symbol | null | undefined): Promise<void> {
  const targetKind = routeName === 'family-space' ? 'lineage' : routeName === 'home' ? 'household' : null
  if (targetKind === null) return
  const family = familySpaceOptions.value.find((option) => option.value === selectedFamilySpaceId.value)
  if (!family) return
  const targetId = targetKind === 'lineage' ? family.lineageId : family.householdId
  if (targetId === null || targetId === spaces.currentSpaceId) return
  await spaceContext.switchSpace(targetId)
}

watch(
  // 路由或空间列表/当前空间完成恢复后，把当前家族对齐到家庭卡/家族树。
  // currentSpaceId 必须参与触发：硬刷新时默认空间恢复可能晚于路由解析，
  // 只监听 route.name 会把 household 上下文留在 family-space 页面。
  [() => route.name, () => spaces.spaces.length, () => spaces.currentSpaceId],
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
        <NSelect class="space-select" :value="selectedFamilySpaceId" :options="spacePickerOptions"
          :render-label="renderSpaceOptionLabel" :loading="switching" placeholder="选择空间"
          size="medium" :consistent-menu-width="false" aria-label="切换当前空间"
          data-test="space-picker" @update:value="onSpaceSelect" />
      </div>
      <div v-if="spaces.membersError" class="sidebar-error" role="alert">
        成员信息加载失败，请重试。
      </div>
      <nav class="sidebar-nav" aria-label="主导航">
        <RouterLink v-for="entry in sidebarPrimaryEntries" :key="entry.name" class="nav-link"
          :to="{ name: entry.name }" :class="{ 'nav-link--active': isNavActive(entry.name) }"
          :aria-current="isNavActive(entry.name) ? 'page' : undefined">
          <component :is="entry.icon" :size="19" aria-hidden="true" />
          <span>{{ entry.label }}</span>
        </RouterLink>
      </nav>
      <div class="sidebar-divider" role="presentation"></div>
      <nav class="sidebar-nav sidebar-nav--secondary" aria-label="次级导航">
        <RouterLink class="nav-link" :to="{ name: 'memory' }"
          :class="{ 'nav-link--active': isNavActive('memory') }"
          :aria-current="isNavActive('memory') ? 'page' : undefined">
          <BookOpen :size="19" aria-hidden="true" /><span>记忆与知识</span>
        </RouterLink>
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
        <div class="topbar-picker" data-test="space-picker-topbar">
          <span class="topbar-picker-caption">家族空间</span>
          <NSelect class="space-select" :value="selectedFamilySpaceId" :options="spacePickerOptions"
            :render-label="renderSpaceOptionLabel" :loading="switching" placeholder="选择空间"
            size="small" :consistent-menu-width="false" aria-label="切换当前空间"
            data-test="space-picker-mobile" @update:value="onSpaceSelect" />
        </div>
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
.nav-link:hover { color: var(--fg-ink); background: var(--fg-surface-sunken); transform: translateX(2px); }
.nav-link--active { color: var(--fg-accent); background: var(--fg-accent-soft); font-weight: 650; box-shadow: inset 3px 0 0 var(--fg-accent); }
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
.shell-topbar::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px; pointer-events: none; background: linear-gradient(90deg, transparent, var(--fg-accent-soft), transparent); }
.shell-brand--topbar { display: none; padding: 0; font-size: 16px; gap: 6px; }
.topbar-location { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--fg-ink-secondary); min-width: 0; }
.topbar-location strong { font-weight: 500; color: var(--fg-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.topbar-picker { display: none; }
.topbar-picker-caption { flex: 0 0 auto; color: var(--fg-ink-secondary); font-size: 11px; }
.topbar-picker :deep(.space-select) { min-width: 0; flex: 1; }
.topbar-actions { display: flex; align-items: center; gap: 10px; min-width: 0; margin-left: auto; }
.topbar-button {
  position: relative; display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; flex: 0 0 auto; padding: 0; border: none; border-radius: 50%;
  background: transparent; color: var(--fg-ink-secondary); cursor: pointer; box-sizing: border-box;
}
.topbar-button:hover { background: var(--fg-surface-sunken); color: var(--fg-ink); }
.topbar-button:focus-visible, .bottom-link:focus-visible, .nav-link:focus-visible { outline-offset: 3px; }
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
.bottom-link { display: flex; flex: 1 1 0; flex-direction: column; align-items: center; justify-content: center; gap: 4px; min-width: 0; min-height: 52px; padding: 6px 8px; color: var(--fg-ink-secondary); font-size: 11px; text-decoration: none; }
.bottom-link:hover { background: var(--fg-surface-sunken); }
.bottom-link--active { color: var(--fg-accent); font-weight: 600; background: var(--fg-accent-soft); }
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
  .shell-topbar { flex-wrap: wrap; gap: 4px; row-gap: 8px; min-height: 64px; padding: 8px 12px; }
  .topbar-picker { display: flex; align-items: center; gap: 8px; flex: 1 1 100%; min-width: 0; order: 3; }
  .topbar-actions { gap: 2px; }
  .theme-switch { margin-right: 2px; }
  .shell-main { padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px)); }
  .shell-bottom-nav { display: flex; padding-bottom: env(safe-area-inset-bottom, 0px); }
}
@media (max-width: 390px) {
  .shell-brand--topbar span { display: none; }
  .shell-brand--topbar { width: 36px; justify-content: center; }
  .topbar-actions { gap: 0; }
  .theme-switch { transform: scale(0.92); transform-origin: right center; }
  .topbar-picker-caption { font-size: 10px; }
  .bottom-link { padding-inline: 4px; font-size: 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .nav-link:hover { transform: none; }
}
</style>
