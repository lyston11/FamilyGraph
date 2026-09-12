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
// - 加载失败按真实原因分类（404 未部署 / 403 无权 / 503 维护 / 网络偏斜），
//   无假数据、无错误横幅泄漏后端细节；
// - 数据全部经 notifications store（服务端真源），页面不发请求。
import { NButton, NEmpty, NSpin } from 'naive-ui'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import ActionCardInbox from '@/components/actioncard/ActionCardInbox.vue'
import NoticeItemRow from '@/components/notifications/NoticeItemRow.vue'
import SuggestionReviewDialog from '@/components/notifications/SuggestionReviewDialog.vue'
import { describeLoadError } from '@/api/loadError'
import { useActionCardsStore } from '@/stores/actionCards'
import { useNotificationsStore } from '@/stores/notifications'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import type { NotificationItem, SuggestionItem } from '@/types/api'
import { classifyNotifications } from '@/types/notifications'

const router = useRouter()
const spaces = useSpacesStore()
const notifications = useNotificationsStore()
const actionCards = useActionCardsStore()
const suggestions = useStewardSuggestionsStore()

function goHome(): void {
  void router.push({ name: 'home' })
}
const pfv = usePersonalFamilyViewStore()

const spaceId = computed(() => spaces.currentSpaceId)
const page = computed(() => (spaceId.value === null ? null : notifications.forSpace(spaceId.value)))
const loading = computed(() => spaceId.value !== null && notifications.isLoading(spaceId.value))
const loadError = computed(() =>
  spaceId.value === null ? null : notifications.errorFor(spaceId.value),
)

/** 按真实失败原因分类的可行动文案（404 未部署 / 403 无权 / 503 维护 / 网络偏斜） */
const errorCopy = computed(() =>
  loadError.value === null ? null : describeLoadError(loadError.value, '通知'),
)

const sections = computed(() => classifyNotifications(page.value?.items ?? []))

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
  // 待核实建议（Steward 投影）：与通知并行加载；失败不阻塞通知分区
  await suggestions.load(spaceId.value).catch(() => undefined)
}

onMounted(() => {
  void load()
})

watch(spaceId, () => {
  // 切换空间：收起 ActionCard 面板与建议弹层并按新上下文重读（store 已按空间清理）
  inboxOpened.value = false
  reviewOpened.value = false
  reviewSuggestion.value = null
  void load()
})

/** 打开通知 = 仅标记已读（服务端已读命令 + 重读列表）；不改任何领域/卡片状态 */
function openNotification(item: NotificationItem): void {
  if (item.read_at !== null || spaceId.value === null) return
  void notifications.markRead(spaceId.value, item.id).catch(() => undefined)
}

// ---- 待核实建议详情弹层：打开只读；提交/驳回在弹层内显式触发 ----
const reviewOpened = ref(false)
const reviewSuggestion = ref<SuggestionItem | null>(null)

function openSuggestion(item: NotificationItem): void {
  if (item.read_at === null && spaceId.value !== null) {
    void notifications.markRead(spaceId.value, item.id).catch(() => undefined)
  }
  const id = item.suggestion?.suggestion_id
  if (id === undefined) return
  const found =
    suggestions.forSpace(spaceId.value ?? 0)?.items.find((s) => s.id === id) ?? null
  reviewSuggestion.value = found
  reviewOpened.value = true
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
    <!-- 顶部行与家庭首页 exit-row 同款：左返回、右动作，卡片上方留白对齐家庭页 -->
    <div class="exit-row">
      <button class="back-link" type="button" data-test="notifications-back" @click="goHome">
        ← 返回我的家庭
      </button>
      <NButton
        size="small"
        secondary
        :disabled="loading || unreadCount === 0"
        data-test="mark-all-read"
        @click="markAllRead"
      >
        全部标记已读
      </NButton>
    </div>
    <article class="notice-hero">
      <header class="hero-head">
        <div class="hero-identity">
          <p class="hero-kind">家庭空间</p>
          <h1 class="hero-title">通知与待办</h1>
          <p class="hero-subtitle">
            当前空间：{{ spaces.currentSpace?.name ?? '未选择' }} ·
            打开通知只会标记已读，不会改变任何申请或连接状态。
          </p>
        </div>
      </header>

    <NSpin v-if="loading && page === null" :show="true" class="loading-spin" />

    <!-- 加载失败：按真实原因分类（404 未部署 / 403 无权 / 503 维护 / 网络偏斜） -->
    <section
      v-else-if="errorCopy !== null"
      class="status-panel"
      data-test="notifications-error"
    >
      <h2 class="status-title">{{ errorCopy.title }}</h2>
      <p class="status-text">{{ errorCopy.text }}</p>
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

      <!-- 分区 2：待核实（Steward 建议投影；与确定性推荐分开，只读详情 + 显式确认） -->
      <section class="notice-section" data-test="section-verify">
        <h2 class="section-title">待核实</h2>
        <p class="section-hint">
          Steward 基于已确认事实发现的可疑/缺失线索；查看详情不会提交任何动作。
        </p>
        <NEmpty
          v-if="sections.verify.length === 0"
          description="没有待核实的线索"
          size="small"
          data-test="verify-empty"
        />
        <ul v-else class="notice-list">
          <li
            v-for="item in sections.verify"
            :key="item.id"
            class="notice-item"
            :class="{ 'notice-item--unread': item.read_at === null }"
            data-test="verify-item"
            @click="openSuggestion(item)"
          >
            <NoticeItemRow :item="item">
              <template #actions>
                <NButton
                  size="small"
                  secondary
                  data-test="open-details"
                  @click.stop="openSuggestion(item)"
                >
                  查看详情
                </NButton>
              </template>
            </NoticeItemRow>
          </li>
        </ul>
      </section>

      <!-- 分区 3：通知 -->
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

      <!-- 分区 4：已完成·历史（read_at 有值且领域终态） -->
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

    </template>

    <!-- 建议详情/确认弹层（本页不直接执行命令；动作在弹层内显式触发） -->
    <SuggestionReviewDialog
      v-if="spaceId !== null"
      v-model:opened="reviewOpened"
      v-model:suggestion="reviewSuggestion"
      :space-id="spaceId"
    />
    </article>
  </main>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  /* 容器几何与家庭首页 household-card-view（1320px / 44px 边距 / 20px 行距）保持一致 */
  gap: 20px;
  max-width: 1320px;
  margin: 0 auto;
  padding: 32px 44px 48px;
  box-sizing: border-box;
}

/* 顶部行同款家庭首页 exit-row：卡片上方留白（32 + 44 + 20）与家庭页一致 */
.exit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}


/* 09-06 视觉补齐：与家庭首页 family-space-hero 同套大卡设计语言（含 580px 最小卡高） */
.notice-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 580px;
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
  .notice-hero { background: var(--fg-surface-raised); }
}

@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .notice-hero:hover {
    transform: translateY(-4px);
    border-top-color: color-mix(in srgb, var(--fg-ink) 45%, transparent);
    box-shadow: var(--fg-shadow-raised), 0 8px 0 -5px var(--fg-glass-border), inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 15%, transparent);
  }
}

.hero-head {
  padding-bottom: 32px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.hero-identity { min-width: 0; }

/* 返回链接移到卡片上方顶部行后，按家庭页 exit-button 同规格（44px 点按目标） */
.back-link {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  padding: 0 6px;
  border: 0;
  background: transparent;
  color: var(--fg-ink-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: color 180ms ease;
}

.back-link:hover { color: var(--fg-accent); }

.hero-kind { margin: 0 0 10px; font-size: 12px; color: var(--fg-ink-secondary); }

.hero-title {
  margin: 0;
  color: var(--fg-ink);
  font-family: var(--fg-font-display);
  font-size: 32px;
  line-height: 1.4;
  font-weight: 600;
}

.hero-subtitle { margin: 6px 0 0; color: var(--fg-ink-secondary); font-size: 12px; line-height: 1.7; }

.loading-spin { min-height: 160px; }

/* 内部分区：大卡内部以分隔线组织（不再各自成卡）；节奏对齐首页卡高度 */
.status-panel,
.notice-section {
  padding: 14px 0 12px;
  border-bottom: 1px solid var(--fg-glass-border);
}

/* 空态压缩：NEmpty 默认图标+留白过大，是卡片偏高的主因 */
.status-panel :deep(.n-empty),
.notice-section :deep(.n-empty) {
  --n-icon-size: 30px;
  --n-text-color: var(--fg-ink-faint);
  padding: 2px 0 0;
  min-height: 0;
}

.status-panel :deep(.n-empty .n-empty__icon),
.notice-section :deep(.n-empty .n-empty__icon) {
  height: 30px;
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
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--fg-ink);
}

.section-hint {
  margin: 2px 0 8px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.notice-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.notice-item {
  padding: 10px 14px;
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  cursor: pointer;
  min-height: 44px;
  box-sizing: border-box;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.notice-item:hover {
  transform: translateX(4px);
  border-color: var(--fg-accent);
  box-shadow:
    0 4px 16px color-mix(in srgb, var(--fg-ink) 8%, transparent),
    0 0 12px var(--fg-glass-glow);
}

.notice-item--unread {
  border-color: var(--fg-accent);
  box-shadow: 0 0 0 1px var(--fg-accent);
}

.notice-item--history {
  cursor: default;
  opacity: 0.82;
}



/* 移动端（≤600px）：单列分区已就绪；头部动作按钮补足 44px 点按目标 */
@media (max-width: 600px) {
  .notice-hero { padding: 24px 20px 20px; }
  .notifications-view :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
