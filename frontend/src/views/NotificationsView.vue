<script setup lang="ts">
// 通知与待办（PRD §2.6 / design §5.4，09-01 Phase 5）：
// - 三分区：待我处理（ActionCard 引用且未处理）/ 通知 / 已完成·历史
//   （read_at 有值且领域终态）；归类按服务端字段（types/notifications.ts）；
// - 打开通知只标记已读（notifications store markRead = 服务端已读命令 +
//   重读列表），绝不改变 ActionCard 状态或领域状态；「全部标记已读」同理；
// - ActionCard 类通知提供「去处理」：打开既有 ActionCardInbox 流程
//   （actionCards store + ActionCardItem），本页自身不执行 accept/reject/execute；
// - 领域终态展示为状态标签；bridge active 通知出现时触发
//   personalFamilyView.reloadAfterBridgeChange(spaceId)（只读重载投影，只触发一次）；
// - 空间管理员对 bridge 通知只有查看权：本页不渲染任何
//   approve/reject/consent/revoke 控件（所有卡片操作都在既有 ActionCard 流程内）；
// - 通知端点运行时 404（BLOCKER 合同占位）→ 安静的「合同未就绪」安全态，无假数据；
// - 数据全部经 notifications store（服务端真源），页面不发请求。
import { NAlert, NButton, NEmpty, NSpin } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'

import ActionCardInbox from '@/components/actioncard/ActionCardInbox.vue'
import NoticeItemRow from '@/components/notifications/NoticeItemRow.vue'
import { ApiError } from '@/api/errors'
import { useActionCardsStore } from '@/stores/actionCards'
import { useNotificationsStore } from '@/stores/notifications'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type { NotificationItem } from '@/types/api'
import { classifyNotifications } from '@/types/notifications'

const spaces = useSpacesStore()
const notifications = useNotificationsStore()
const actionCards = useActionCardsStore()
const pfv = usePersonalFamilyViewStore()

const spaceId = computed(() => spaces.currentSpaceId)
const page = computed(() => (spaceId.value === null ? null : notifications.forSpace(spaceId.value)))
const loading = computed(() => spaceId.value !== null && notifications.isLoading(spaceId.value))
const loadError = computed(() =>
  spaceId.value === null ? null : notifications.errorFor(spaceId.value),
)

/** 404（BLOCKER 端点未落地）→ 安静的合同未就绪态；其他错误 → 可重试错误态 */
const contractUnready = computed(
  () => loadError.value instanceof ApiError && loadError.value.status === 404,
)

const sections = computed(() => classifyNotifications(page.value?.items ?? []))
const hasItems = computed(() => (page.value?.items.length ?? 0) > 0)

/** 全部已读：仅影响已读状态；unread_count 来自服务端载荷 */
const unreadCount = computed(() =>
  spaceId.value === null ? 0 : notifications.unreadCountOf(spaceId.value),
)

// ---- ActionCardInbox 受控展开（既有 ActionCard 流程，本页不执行卡片动作） ----
const inboxOpened = ref(false)

async function goProcess(): Promise<void> {
  if (spaceId.value === null) return
  inboxOpened.value = true
  await actionCards.ensureLoaded(spaceId.value).catch(() => undefined)
}

// ---- bridge active：通知展示后触发当前空间投影只读重载（每次出现只触发一次） ----
let bridgeReloadTriggered = false

const hasActiveBridge = computed(() =>
  (page.value?.items ?? []).some(
    (item) => item.kind === 'bridge' && item.domain_status === 'active',
  ),
)

watch(hasActiveBridge, (active) => {
  if (!active) {
    bridgeReloadTriggered = false
    return
  }
  const currentSpaceId = spaceId.value
  if (!bridgeReloadTriggered && currentSpaceId !== null) {
    bridgeReloadTriggered = true
    void pfv.reloadAfterBridgeChange(currentSpaceId).catch(() => undefined)
  }
})

async function load(): Promise<void> {
  if (spaceId.value === null) return
  await notifications.load(spaceId.value).catch(() => undefined)
}

onMounted(() => {
  void load()
})

watch(spaceId, () => {
  // 切换空间：收起 ActionCard 面板并按新上下文重读（store 已按空间清理）
  inboxOpened.value = false
  void load()
})

/** 打开通知 = 仅标记已读（服务端已读命令 + 重读列表）；不改任何领域/卡片状态 */
function openNotification(item: NotificationItem): void {
  if (item.read_at !== null || spaceId.value === null) return
  void notifications.markRead(spaceId.value, item.id).catch(() => undefined)
}

function markAllRead(): void {
  if (spaceId.value === null) return
  void notifications.markAllRead(spaceId.value).catch(() => undefined)
}

function retry(): void {
  void notifications.refresh(spaceId.value ?? 0).catch(() => undefined)
}
</script>

<template>
  <main class="notifications-view" data-test="notifications-view">
    <header class="view-head">
      <div>
        <h1 class="view-title">通知与待办</h1>
        <p class="view-subtitle">
          当前空间：{{ spaces.currentSpace?.name ?? '未选择' }} ·
          打开通知只会标记已读，不会改变任何申请或连接状态。
        </p>
      </div>
      <NButton
        size="small"
        secondary
        :disabled="loading || unreadCount === 0"
        data-test="mark-all-read"
        @click="markAllRead"
      >
        全部标记已读
      </NButton>
    </header>

    <NSpin v-if="loading && page === null" :show="true" class="loading-spin" />

    <!-- 通知端点 404（BLOCKER 合同占位）：安静的安全态，无假数据、无错误横幅 -->
    <section
      v-else-if="contractUnready"
      class="status-panel"
      data-test="notifications-contract-unready"
    >
      <h2 class="status-title">通知服务合同未就绪</h2>
      <p class="status-text">
        通知中心的服务端合同尚未落地或暂时不可用。已按安全策略不展示任何通知数据；待办仍可从各流程入口进入处理。
      </p>
      <NButton size="small" data-test="notifications-retry" @click="retry">重新加载</NButton>
    </section>

    <!-- 其他错误：可解释失败态（不退化成普通空状态） -->
    <section v-else-if="loadError !== null" class="status-panel" data-test="notifications-error">
      <h2 class="status-title">通知暂时无法加载</h2>
      <p class="status-text">网络或服务暂时不可用，请稍后重试。</p>
      <NButton size="small" data-test="notifications-retry" @click="retry">重新加载</NButton>
    </section>

    <template v-else>
      <!-- 分区 1：待我处理（ActionCard 引用且未处理） -->
      <section class="notice-section" data-test="section-pending">
        <h2 class="section-title">待我处理</h2>
        <p class="section-hint">以下建议需要你明确接受或不接受；处理动作在既有卡片流程内完成。</p>
        <!-- ActionCard 处理走既有 Inbox/ActionCardItem 流程（actionCards store） -->
        <ActionCardInbox v-model:opened="inboxOpened" />
        <NEmpty
          v-if="sections.pendingAction.length === 0"
          description="没有等待你处理的待办"
          size="small"
          data-test="pending-empty"
        />
        <ul v-else class="notice-list">
          <li
            v-for="item in sections.pendingAction"
            :key="item.id"
            class="notice-item"
            :class="{ 'notice-item--unread': item.read_at === null }"
            data-test="notice-item"
            @click="openNotification(item)"
          >
            <NoticeItemRow :item="item">
              <template #actions>
                <NButton
                  size="small"
                  type="primary"
                  secondary
                  data-test="go-process"
                  @click.stop="goProcess"
                >
                  去处理
                </NButton>
              </template>
            </NoticeItemRow>
          </li>
        </ul>
      </section>

      <!-- 分区 2：通知 -->
      <section class="notice-section" data-test="section-notices">
        <h2 class="section-title">通知</h2>
        <NEmpty
          v-if="sections.notices.length === 0"
          description="暂无通知"
          size="small"
          data-test="notices-empty"
        />
        <ul v-else class="notice-list">
          <li
            v-for="item in sections.notices"
            :key="item.id"
            class="notice-item"
            :class="{ 'notice-item--unread': item.read_at === null }"
            data-test="notice-item"
            @click="openNotification(item)"
          >
            <NoticeItemRow :item="item" />
          </li>
        </ul>
      </section>

      <!-- 分区 3：已完成·历史（read_at 有值且领域终态） -->
      <section class="notice-section" data-test="section-history">
        <h2 class="section-title">已完成 · 历史</h2>
        <NEmpty
          v-if="sections.history.length === 0"
          description="暂无已完成或历史记录"
          size="small"
          data-test="history-empty"
        />
        <ul v-else class="notice-list">
          <li
            v-for="item in sections.history"
            :key="item.id"
            class="notice-item notice-item--history"
            data-test="notice-item"
          >
            <NoticeItemRow :item="item" />
          </li>
        </ul>
      </section>

      <!-- 无任何通知：整体空态 -->
      <NAlert
        v-if="!hasItems"
        type="info"
        :show-icon="true"
        :closable="false"
        class="all-empty"
        data-test="notifications-all-empty"
      >
        当前空间暂无通知。
      </NAlert>
    </template>
  </main>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 860px;
  margin: 0 auto;
  padding: 20px 16px 40px;
  box-sizing: border-box;
}

.view-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.view-title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  color: var(--fg-ink);
}

.view-subtitle {
  margin: 4px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.loading-spin {
  min-height: 160px;
}

.status-panel,
.notice-section {
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

.section-title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.section-hint {
  margin: 0 0 10px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.notice-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.notice-item {
  padding: 12px;
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
}

.notice-item--unread {
  border-color: var(--fg-accent);
}

.notice-item--history {
  cursor: default;
  opacity: 0.82;
}

.all-empty {
  border: none;
}

/* 移动端（≤600px）：单列分区已就绪；头部动作按钮补足 44px 点按目标 */
@media (max-width: 600px) {
  .notifications-view :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
