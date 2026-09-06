<script setup lang="ts">
// 当前空间统计（PRD §2.6 / design §5.4，09-01 Phase 5）：
// - 只消费 spaceStats store（GET /stats?space_id=，服务端授权聚合）：
//   授权节点/关系/成员、关系分布（dir_class）、待确认事项、视图状态（6 态）、
//   computed_at/更新时间；household 显示家庭授权聚合，lineage 显示当前
//   PersonalFamilyView 聚合；不做跨空间总计；
// - 不从前端节点数组推导任何统计，也不显示隐藏对象/未授权分支规模；
// - 端点 404（BLOCKER 合同占位）→「统计服务合同未就绪」安全状态面板，
//   绝不回退旧 /stats 无空间合同；
// - 页面不发请求，一切经 store；旧 api/stats.ts 已删除（无消费方）。
import { NAlert, NButton, NSpin } from 'naive-ui'
import { computed, onMounted, watch } from 'vue'

import { ApiError } from '@/api/errors'
import { useSpaceStatsStore } from '@/stores/spaceStats'
import { useSpacesStore } from '@/stores/spaces'
import type { DirClass, SpaceStatsStatus } from '@/types/api'

const spaces = useSpacesStore()
const spaceStats = useSpaceStatsStore()

const spaceId = computed(() => spaces.currentSpaceId)
const currentSpace = computed(() => spaces.currentSpace)
const data = computed(() => (spaceId.value === null ? null : spaceStats.forSpace(spaceId.value)))
const loading = computed(() => spaceId.value !== null && spaceStats.isLoading(spaceId.value))
const loadError = computed(() =>
  spaceId.value === null ? null : spaceStats.errorFor(spaceId.value),
)

/** 404（BLOCKER 端点未落地）→ 合同未就绪安全态；其他错误 → 可重试错误态 */
const contractUnready = computed(
  () => loadError.value instanceof ApiError && loadError.value.status === 404,
)

/** 统计口径说明：household = 家庭授权聚合；lineage = 当前 PersonalFamilyView 聚合 */
const scopeLabel = computed(() => {
  if (data.value?.space_kind === 'household') return '家庭授权聚合'
  if (data.value?.space_kind === 'lineage') return '当前家族视图聚合'
  return currentSpace.value?.kind === 'household' ? '家庭授权聚合' : '当前家族视图聚合'
})

/** 无数据态（6 态中的 4 个）：展示统一状态面板而非数字 */
const NO_DATA_STATES: readonly SpaceStatsStatus[] = ['never_computed', 'queued', 'running', 'failed']
const hasData = computed(
  () => data.value !== null && !NO_DATA_STATES.includes(data.value.status),
)

const maxSlice = computed(() =>
  Math.max(0, ...(data.value?.relation_distribution.map((slice) => slice.count) ?? [0])),
)

const DIR_CLASS_LABELS: Record<DirClass, string> = {
  elder: '长辈',
  younger: '晚辈',
  peer: '同辈',
  spouse: '配偶',
}

/** 6 态状态面板文案（never_computed/queued/running/failed 无数据时展示） */
const STATUS_PANEL_TEXT: Record<SpaceStatsStatus, { title: string; text: string }> = {
  never_computed: {
    title: '统计尚未计算',
    text: '该空间的统计投影还没有生成。完成建档与关系确认后，系统会自动计算授权范围内的统计。',
  },
  queued: {
    title: '统计排队中',
    text: '统计计算已排队，稍后会自动更新。',
  },
  running: {
    title: '统计计算中',
    text: '正在按当前空间授权口径计算统计，稍后自动更新。',
  },
  current: { title: '', text: '' },
  stale: { title: '', text: '' },
  failed: {
    title: '统计计算失败',
    text: '最近一次统计计算未成功。已按安全策略不展示可能过期的数字。',
  },
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '—'
}

async function load(): Promise<void> {
  if (spaceId.value === null) return
  await spaceStats.load(spaceId.value).catch(() => undefined)
}

function refresh(): void {
  void spaceStats.refresh(spaceId.value ?? 0).catch(() => undefined)
}

onMounted(() => {
  void load()
})

watch(spaceId, () => {
  // 只按当前空间请求：切换空间后重读新空间投影（store 已按空间清理）
  void load()
})
</script>

<template>
  <main class="stats-view" data-test="stats-view">
    <header class="view-head">
      <div>
        <h1 class="view-title">统计</h1>
        <p class="view-subtitle">
          当前空间：{{ currentSpace?.name ?? '未选择' }} · {{ scopeLabel }} ·
          不做跨空间总计，隐藏对象与未授权分支不计入。
        </p>
      </div>
      <NButton
        size="small"
        secondary
        :disabled="loading"
        data-test="stats-refresh"
        @click="refresh"
      >
        刷新
      </NButton>
    </header>

    <NSpin v-if="loading && data === null" :show="true" class="loading-spin" />

    <!-- 404（BLOCKER 合同占位）：统计服务合同未就绪安全态，不回退旧 /stats -->
    <section
      v-else-if="contractUnready"
      class="status-panel"
      data-test="stats-contract-unready"
    >
      <h2 class="status-title">统计服务合同未就绪</h2>
      <p class="status-text">
        空间化统计的服务端合同尚未落地或暂时不可用。已按安全策略不展示任何统计数字；
        统计只能来自服务端授权聚合，不会由页面从节点数据推算。
      </p>
      <NButton size="small" data-test="stats-retry" @click="refresh">重新加载</NButton>
    </section>

    <!-- 其他错误：可解释失败态（不退化成普通空状态） -->
    <section v-else-if="loadError !== null" class="status-panel" data-test="stats-error">
      <h2 class="status-title">统计暂时无法加载</h2>
      <p class="status-text">网络或服务暂时不可用，请稍后重试。</p>
      <NButton size="small" data-test="stats-retry" @click="refresh">重新加载</NButton>
    </section>

    <template v-else-if="data !== null">
      <!-- 视图状态（6 态）：无数据态展示统一状态面板 -->
      <section
        v-if="!hasData"
        class="status-panel"
        data-test="stats-status-panel"
      >
        <h2 class="status-title">{{ STATUS_PANEL_TEXT[data.status].title }}</h2>
        <p class="status-text">{{ STATUS_PANEL_TEXT[data.status].text }}</p>
        <p class="status-meta">
          状态：{{ data.status }}<template v-if="data.stale_reason"> · {{ data.stale_reason }}</template>
        </p>
      </section>

      <template v-else>
        <!-- stale：数据 + 明确过期标注（不静默展示旧数字） -->
        <NAlert
          v-if="data.status === 'stale'"
          type="warning"
          :show-icon="true"
          :closable="false"
          data-test="stats-stale-alert"
        >
          统计可能已过期{{ data.stale_reason ? `：${data.stale_reason}` : '' }}。可刷新获取最新授权聚合。
        </NAlert>

        <!-- 授权摘要卡：服务端聚合（当前空间，无跨空间总计） -->
        <section class="summary-cards" data-test="stats-summary">
          <div class="summary-card">
            <strong data-test="stat-node-count">{{ data.node_count }}</strong>
            <span>授权节点</span>
          </div>
          <div class="summary-card">
            <strong data-test="stat-edge-count">{{ data.edge_count }}</strong>
            <span>授权关系</span>
          </div>
          <div class="summary-card">
            <strong data-test="stat-member-count">{{ data.member_count }}</strong>
            <span>空间成员</span>
          </div>
        </section>

        <!-- 待确认事项 -->
        <section class="pending-section" data-test="stats-pending">
          <h2 class="section-title">待确认事项</h2>
          <div class="summary-cards">
            <div class="summary-card">
              <strong data-test="stat-pending-cards">{{ data.pending_action_cards }}</strong>
              <span>待处理建议</span>
            </div>
            <div class="summary-card">
              <strong data-test="stat-pending-memberships">{{ data.pending_memberships }}</strong>
              <span>待确认成员申请</span>
            </div>
          </div>
        </section>

        <!-- 关系分布（dir_class，服务端口径） -->
        <section class="distribution-section" data-test="stats-distribution">
          <h2 class="section-title">关系分布</h2>
          <p
            v-if="data.relation_distribution.length === 0"
            class="hint"
            data-test="distribution-empty"
          >
            当前授权范围内暂无已确认的关系。
          </p>
          <div v-for="slice in data.relation_distribution" :key="slice.dir_class" class="bar-row">
            <span class="bucket">{{ DIR_CLASS_LABELS[slice.dir_class] }}</span>
            <div class="bar-track">
              <div
                class="bar"
                :style="{ width: maxSlice === 0 ? '0%' : `${(slice.count / maxSlice) * 100}%` }"
              />
            </div>
            <span class="count">{{ slice.count }}</span>
          </div>
        </section>

        <!-- 数据版本与更新时间 -->
        <section class="meta-section" data-test="stats-meta">
          <span>数据版本：{{ data.view_version === null ? '—' : `v${data.view_version}` }}</span>
          <span data-test="stats-computed-at">更新时间：{{ formatTime(data.computed_at) }}</span>
          <span>状态：{{ data.status }}</span>
        </section>
      </template>
    </template>

    <!-- 无当前空间：安全上下文提示 -->
    <section v-else class="status-panel" data-test="stats-no-space">
      <h2 class="status-title">未选择空间</h2>
      <p class="status-text">统计跟随当前选中空间。请先在顶部选择一个家庭或家族空间。</p>
    </section>
  </main>
</template>

<style scoped>
.stats-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 980px;
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
.pending-section,
.distribution-section,
.meta-section {
  padding: 20px 24px;
  background: var(--fg-glass-surface);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  box-shadow:
    0 4px 20px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 20%, transparent);
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 18px 20px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.4);
  box-shadow:
    0 4px 16px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 25%, transparent);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.pending-section .summary-cards {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.summary-card:hover {
  transform: translateY(-3px);
  border-color: color-mix(in srgb, var(--fg-accent) 40%, var(--fg-glass-border));
  box-shadow:
    0 8px 24px color-mix(in srgb, var(--fg-ink) 10%, transparent),
    0 0 16px var(--fg-glass-glow);
}

.summary-card strong {
  font-family: var(--fg-font-display);
  font-size: 26px;
  font-weight: 700;
  color: var(--fg-ink);
}

.summary-card span {
  color: var(--fg-ink-secondary);
  font-size: 12px;
}

.distribution-section .bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.bucket {
  width: 48px;
  font-size: 13px;
  text-align: right;
  color: var(--fg-ink-secondary);
}

.bar-track {
  flex: 1;
  height: 14px;
  background: var(--fg-surface-sunken);
  border-radius: 7px;
  overflow: hidden;
}

.bar {
  height: 100%;
  background: var(--fg-accent);
  border-radius: 7px;
}

.count {
  width: 32px;
  font-size: 13px;
  color: var(--fg-ink);
}

.hint {
  margin: 0;
  color: var(--fg-ink-secondary);
  font-size: 13px;
}

.meta-section {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 18px;
  padding: 12px 16px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
}

@media (max-width: 600px) {
  .summary-cards,
  .pending-section .summary-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }

  /* 页面为单列纵排；动作按钮补足 44px 点按目标 */
  .stats-view :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
