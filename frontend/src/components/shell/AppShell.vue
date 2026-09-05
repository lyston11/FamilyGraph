<script setup lang="ts">
// 统一家庭用户应用壳（09-01 design.md §3.1）：
// - 桌面端左侧固定导航：一级入口 = 我的家庭 / 家族树 / 记忆与知识 / 统计，
//   次级 = 设置；「空间管理」仅当前空间 space_admin 权限成立时出现；
// - 顶部：通知入口（铃铛 + 未读数，端点 404 时安静降级无角标）、Assistant
//   入口占位（disabled，依赖 Agent Runtime 任务）、主题切换、账号菜单（含登出）；
// - 空间选择器按 household/lineage 分组，切换触发 useSpaceContext 空间切换事务；
// - 主内容区为静态星空/点阵背景层（纯 CSS，无动画/粒子，token 派生，不承载数据语义）。
// 旧全局搜索（GlobalSearch，走旧 /search 合同）已从壳移除：新壳导航不含全局搜索，
// 旧全局搜索与授权边界冲突，后续按空间内检索另行设计（记录见任务 notes.md）。
import { NPopover, NSelect, NSwitch, type SelectOption } from 'naive-ui'
import { computed, h, ref, watch, type VNodeChild } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useNotificationsStore } from '@/stores/notifications'
import { useSpacesStore } from '@/stores/spaces'
import { useUiStore } from '@/stores/ui'
import { useSpaceContext } from '@/composables/useSpaceContext'

const SPACE_KIND_GROUP_LABELS = { household: '家庭空间', lineage: '家族空间' } as const

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const ui = useUiStore()
const notifications = useNotificationsStore()
const spaceContext = useSpaceContext()

const switching = ref(false)

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
  for (const kind of ['household', 'lineage'] as const) {
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
  <div class="app-shell">
    <aside class="shell-sidebar">
      <RouterLink
        class="shell-brand"
        :to="{ name: 'home' }"
        aria-label="FamilyGraph 我的家庭"
      >
        FamilyGraph
      </RouterLink>

      <div class="sidebar-picker">
        <NSelect
          class="space-select"
          :value="spaces.currentSpaceId"
          :options="spacePickerOptions"
          :render-label="renderSpaceOptionLabel"
          :loading="switching"
          placeholder="选择空间"
          size="medium"
          :consistent-menu-width="false"
          aria-label="切换当前空间"
          data-test="space-picker"
          @update:value="onSpaceSelect"
        />
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <RouterLink
          class="nav-link"
          :to="{ name: 'home' }"
          :class="{ 'nav-link--active': isNavActive('home') }"
          :aria-current="isNavActive('home') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 11.5 12 4.5l8 7V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z" />
          </svg>
          <span>我的家庭</span>
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'family-space' }"
          :class="{ 'nav-link--active': isNavActive('family-space') }"
          :aria-current="isNavActive('family-space') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="5" r="2.2" />
            <circle cx="6" cy="18" r="2.2" />
            <circle cx="18" cy="18" r="2.2" />
            <path d="M12 7.2v4.3M6 15.8v-1.6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1.6" />
          </svg>
          <span>家族树</span>
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'memory' }"
          :class="{ 'nav-link--active': isNavActive('memory') }"
          :aria-current="isNavActive('memory') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15.5H7.5A2.5 2.5 0 0 0 5 21z" />
            <path d="M5 18.5A2.5 2.5 0 0 1 7.5 16H19" />
          </svg>
          <span>记忆与知识</span>
        </RouterLink>
        <RouterLink
          class="nav-link"
          :to="{ name: 'stats' }"
          :class="{ 'nav-link--active': isNavActive('stats') }"
          :aria-current="isNavActive('stats') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 20V12M12 20V5M19 20v-9" />
          </svg>
          <span>统计</span>
        </RouterLink>
      </nav>

      <div class="sidebar-divider" role="presentation"></div>

      <nav class="sidebar-nav sidebar-nav--secondary" aria-label="次级导航">
        <RouterLink
          class="nav-link"
          :to="{ name: 'settings' }"
          :class="{ 'nav-link--active': isNavActive('settings') }"
          :aria-current="isNavActive('settings') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path
              d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M18 6l-1.6 1.6M7.6 16.4 6 18"
            />
          </svg>
          <span>设置</span>
        </RouterLink>
        <RouterLink
          v-if="canManageCurrentSpace"
          class="nav-link"
          :to="spaceManagementTarget"
          :class="{ 'nav-link--active': isNavActive('space-management') }"
          :aria-current="isNavActive('space-management') ? 'page' : undefined"
          data-test="space-management-link"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3.5 18.5 6v5.2c0 4.6-3 7.6-6.5 8.8-3.5-1.2-6.5-4.2-6.5-8.8V6z" />
          </svg>
          <span>空间管理</span>
        </RouterLink>
      </nav>
    </aside>

    <div class="shell-body">
      <header class="shell-topbar">
        <RouterLink
          class="shell-brand shell-brand--topbar"
          :to="{ name: 'home' }"
          aria-label="FamilyGraph 我的家庭"
        >
          FamilyGraph
        </RouterLink>

        <div class="topbar-picker">
          <NSelect
            class="space-select"
            :value="spaces.currentSpaceId"
            :options="spacePickerOptions"
            :render-label="renderSpaceOptionLabel"
            :loading="switching"
            placeholder="选择空间"
            size="medium"
            :consistent-menu-width="false"
            aria-label="切换当前空间"
            data-test="space-picker-topbar"
            @update:value="onSpaceSelect"
          />
        </div>

        <div class="topbar-actions">
          <RouterLink
            class="topbar-button"
            :to="{ name: 'notifications' }"
            aria-label="通知"
            data-test="notifications-entry"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 9.5a6 6 0 0 1 12 0c0 4.6 1.8 5.8 1.8 5.8H4.2S6 14.1 6 9.5Z" />
              <path d="M10.4 19.5a1.7 1.7 0 0 0 3.2 0" />
            </svg>
            <span
              v-if="unreadCount > 0"
              class="unread-badge"
              data-test="notifications-unread"
            >
              {{ unreadCount > 99 ? '99+' : unreadCount }}
            </span>
          </RouterLink>

          <!-- Assistant 面板入口占位：依赖 Agent Runtime 任务（独立任务交付），本期不实现面板 -->
          <button
            class="topbar-button"
            type="button"
            disabled
            aria-label="助手（即将提供）"
            title="助手功能即将提供"
            data-test="assistant-placeholder"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 3.5 13.9 9 19.5 11 13.9 13 12 18.5 10.1 13 4.5 11 10.1 9z" />
            </svg>
          </button>

          <NSwitch
            class="theme-switch"
            :value="ui.theme === 'modern'"
            aria-label="切换配色主题（纸墨 / 清雅）"
            @update:value="onThemeSwitch"
          >
            <template #checked>清雅</template>
            <template #unchecked>纸墨</template>
          </NSwitch>

          <NPopover trigger="click" placement="bottom-end">
            <template #trigger>
              <button
                class="topbar-button"
                type="button"
                aria-label="账号菜单"
                data-test="account-menu-trigger"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
                  <path
                    d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12Zm0 2.25c-3.6 0-7.5 1.8-7.5 4.5v1.13c0 .48.39.87.87.87h13.26c.48 0 .87-.39.87-.87v-1.13c0-2.7-3.9-4.5-7.5-4.5Z"
                  />
                </svg>
              </button>
            </template>
            <div class="account-menu" role="menu" aria-label="账号菜单">
              <p class="account-name" data-test="account-menu-name">
                {{ auth.user?.name ?? '已登录' }}
              </p>
              <button
                class="account-item"
                type="button"
                role="menuitem"
                data-test="account-menu-settings"
                @click="goSettings"
              >
                设置
              </button>
              <button
                v-if="canManageCurrentSpace"
                class="account-item"
                type="button"
                role="menuitem"
                data-test="account-menu-space-management"
                @click="goSpaceManagement"
              >
                空间管理
              </button>
              <button
                class="account-item account-item--danger"
                type="button"
                role="menuitem"
                data-test="account-menu-logout"
                @click="handleLogout"
              >
                登出
              </button>
            </div>
          </NPopover>
        </div>
      </header>

      <main class="shell-main">
        <RouterView />
      </main>

      <nav class="shell-bottom-nav" aria-label="底部导航">
        <RouterLink
          class="bottom-link"
          :to="{ name: 'home' }"
          :class="{ 'bottom-link--active': isNavActive('home') }"
          :aria-current="isNavActive('home') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 11.5 12 4.5l8 7V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z" />
          </svg>
          <span>我的家庭</span>
        </RouterLink>
        <RouterLink
          class="bottom-link"
          :to="{ name: 'family-space' }"
          :class="{ 'bottom-link--active': isNavActive('family-space') }"
          :aria-current="isNavActive('family-space') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="5" r="2.2" />
            <circle cx="6" cy="18" r="2.2" />
            <circle cx="18" cy="18" r="2.2" />
            <path d="M12 7.2v4.3M6 15.8v-1.6a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1.6" />
          </svg>
          <span>家族树</span>
        </RouterLink>
        <RouterLink
          class="bottom-link"
          :to="{ name: 'memory' }"
          :class="{ 'bottom-link--active': isNavActive('memory') }"
          :aria-current="isNavActive('memory') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15.5H7.5A2.5 2.5 0 0 0 5 21z" />
            <path d="M5 18.5A2.5 2.5 0 0 1 7.5 16H19" />
          </svg>
          <span>记忆</span>
        </RouterLink>
        <RouterLink
          class="bottom-link"
          :to="{ name: 'stats' }"
          :class="{ 'bottom-link--active': isNavActive('stats') }"
          :aria-current="isNavActive('stats') ? 'page' : undefined"
        >
          <svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 20V12M12 20V5M19 20v-9" />
          </svg>
          <span>统计</span>
        </RouterLink>
      </nav>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  min-width: 0;
  background-color: var(--fg-surface);
}

/* ---- 左侧固定导航（桌面端） ---- */
.shell-sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 232px;
  flex: 0 0 232px;
  box-sizing: border-box;
  padding: 16px 12px;
  background-color: var(--fg-surface-raised);
  border-right: 1px solid var(--fg-line);
}

.shell-brand {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 8px;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
  text-decoration: none;
  white-space: nowrap;
}

.sidebar-picker {
  min-width: 0;
}

.sidebar-picker :deep(.space-select) {
  width: 100%;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.sidebar-nav--secondary {
  margin-top: -4px;
}

.sidebar-divider {
  height: 1px;
  margin: 4px 8px;
  background-color: var(--fg-line);
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  box-sizing: border-box;
  padding: 8px 12px;
  border-radius: var(--fg-radius-control);
  color: var(--fg-ink-secondary);
  font-size: 14px;
  text-decoration: none;
  white-space: nowrap;
}

.nav-link:hover {
  color: var(--fg-ink);
  background-color: var(--fg-surface-sunken);
}

.nav-link--active {
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  font-weight: 600;
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex: 0 0 auto;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* ---- 右侧主体 ---- */
.shell-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.shell-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  padding: 8px 20px;
  box-sizing: border-box;
  background-color: var(--fg-glass-surface-raised);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border-bottom: 1px solid var(--fg-glass-border);
  box-shadow:
    0 4px 20px color-mix(in srgb, var(--fg-ink) 4%, transparent),
    inset 0 -1px 0 color-mix(in srgb, var(--fg-surface-raised) 15%, transparent);
}

.shell-brand--topbar {
  display: none;
  padding: 0;
  font-size: 18px;
}

.topbar-picker {
  display: none;
  min-width: 0;
  flex: 1 1 auto;
  max-width: 260px;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-left: auto;
}

.topbar-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  padding: 0;
  border: 1px solid var(--fg-line);
  border-radius: 50%;
  background-color: var(--fg-surface);
  color: var(--fg-ink-secondary);
  cursor: pointer;
  box-sizing: border-box;
}

.topbar-button:hover:not(:disabled) {
  border-color: var(--fg-line-strong);
  color: var(--fg-ink);
}

.topbar-button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.unread-badge {
  position: absolute;
  top: -2px;
  right: -4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  box-sizing: border-box;
  border-radius: 9px;
  background-color: var(--fg-accent);
  color: var(--fg-accent-ink);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.theme-switch {
  flex-shrink: 1;
}

.account-menu {
  display: flex;
  flex-direction: column;
  min-width: 160px;
}

.account-name {
  margin: 0;
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg-ink);
  border-bottom: 1px solid var(--fg-line);
}

.account-item {
  display: flex;
  align-items: center;
  min-height: 44px;
  padding: 8px 12px;
  box-sizing: border-box;
  border: none;
  background: none;
  color: var(--fg-ink-secondary);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.account-item:hover {
  color: var(--fg-ink);
  background-color: var(--fg-surface-sunken);
}

.account-item--danger {
  color: var(--fg-status-disputed);
}

/* ---- 主内容区：静态星空/点阵背景层（纯 CSS，无动画/粒子；token 派生，双主题自适应） ---- */
.shell-main {
  position: relative;
  min-width: 0;
  flex: 1;
  background-color: var(--fg-surface);
  background-image:
    radial-gradient(ellipse 80% 50% at 50% -10%, color-mix(in srgb, var(--fg-accent) 10%, transparent), transparent 70%),
    radial-gradient(ellipse 60% 40% at 85% 95%, color-mix(in srgb, var(--fg-info) 8%, transparent), transparent 60%),
    radial-gradient(color-mix(in srgb, var(--fg-ink) 22%, transparent) 1.5px, transparent 1.8px),
    radial-gradient(color-mix(in srgb, var(--fg-accent) 18%, transparent) 1.2px, transparent 1.5px),
    radial-gradient(color-mix(in srgb, var(--fg-ink) 8%, transparent) 1px, transparent 1.4px);
  background-size:
    100% 100%,
    100% 100%,
    240px 240px,
    180px 180px,
    28px 28px;
  background-position:
    0 0,
    0 0,
    48px 36px,
    110px 90px,
    0 0;
}

/* ---- 移动端底部一级导航 ---- */
.shell-bottom-nav {
  display: none;
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 100;
  align-items: stretch;
  justify-content: space-around;
  background-color: var(--fg-surface-raised);
  border-top: 1px solid var(--fg-line);
}

.bottom-link {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  min-width: 64px;
  min-height: 52px;
  box-sizing: border-box;
  padding: 4px 8px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
  text-decoration: none;
}

.bottom-link--active {
  color: var(--fg-accent);
  font-weight: 600;
}

.bottom-link .nav-icon {
  width: 22px;
  height: 22px;
}

/* ---- 空间选择选项 ---- */
.sidebar-picker :deep(.space-option),
.topbar-picker :deep(.space-option) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.sidebar-picker :deep(.space-option-name),
.topbar-picker :deep(.space-option-name) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-picker :deep(.space-option-badge),
.topbar-picker :deep(.space-option-badge) {
  flex: 0 0 auto;
  padding: 1px 6px;
  border-radius: 8px;
  background-color: var(--fg-accent-soft);
  color: var(--fg-accent);
  font-size: 11px;
  font-weight: 600;
}

@media (max-width: 768px) {
  .shell-sidebar {
    display: none;
  }

  .shell-brand--topbar {
    display: inline-flex;
    flex: 0 0 auto;
  }

  .shell-topbar {
    /* 移动端两行布局：第一行品牌 + 动作，第二行整行空间选择器——
       375px 竖屏/横屏切换时选择器始终有可用宽度，不挤压不溢出 */
    flex-wrap: wrap;
    row-gap: 6px;
    padding: 8px 12px;
  }

  .topbar-picker {
    /* 整行置底：宽度充足且不与品牌/动作按钮争抢空间（Phase 7 响应式） */
    display: block;
    order: 10;
    flex: 1 1 100%;
    max-width: none;
  }

  .shell-main {
    /* 底部导航高度留白（含 iOS 安全区），避免页面内容被遮挡 */
    padding-bottom: calc(64px + env(safe-area-inset-bottom, 0px));
  }

  .shell-bottom-nav {
    display: flex;
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }
}
</style>
