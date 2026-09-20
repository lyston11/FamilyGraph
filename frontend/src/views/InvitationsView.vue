<script setup lang="ts">
// 邀请与申请（任务 09-20-space-invitation-reachability）。
//
// 为什么需要独立页面：pending 受邀人**不是**该空间 active 成员，读不到该空间的
// 通知（`authorized_space_or_404` 安全 404），而通知中心只按当前空间加载——于是
// 发给我的邀请在任何界面都不可达。本页数据来自跨空间的 `/spaces/invitations`
// 自足投影，**与当前空间无关**。
//
// 授权不作为前端边界：`stage` 只决定按钮是否可点，真正的顺序与拒绝（未获房主批准
// 时接受返回 403）仍由服务端 space_fsm 判定。
import { NButton, NEmpty, NSpin } from 'naive-ui'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { describeLoadError } from '@/api/loadError'
import { ApiError } from '@/api/errors'
import { useSpacesStore } from '@/stores/spaces'
import type { PendingInvitation } from '@/types/api'

const router = useRouter()
const spaces = useSpacesStore()

const loading = ref(false)
const error = ref<unknown>(null)
/** 正在提交的成员行 id（只禁用该行，不影响其他行） */
const actingId = ref<number | null>(null)
const feedback = ref<string | null>(null)
const feedbackError = ref(false)

const incoming = computed(() => spaces.incomingInvitations)
const outgoing = computed(() => spaces.outgoingInvitations)
const loadError = computed(() => (error.value === null ? null : describeLoadError(error.value, '邀请')))

function goHome(): void {
  void router.push({ name: 'home' })
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    await spaces.loadInvitations()
  } catch (cause) {
    error.value = cause
  } finally {
    loading.value = false
  }
}

function describeError(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : '操作失败，请稍后重试'
}

/** 状态文案：把服务端给出的 stage 翻成「球在谁那边」，不在前端重推审批链。 */
function stageLabel(item: PendingInvitation): string {
  if (item.direction === 'outgoing') return '等待房主批准'
  return item.stage === 'awaiting_me' ? '等待你接受' : '等待房主批准'
}

function canRespond(item: PendingInvitation): boolean {
  return item.direction === 'incoming' && item.stage === 'awaiting_me'
}

function counterpartText(item: PendingInvitation): string {
  if (item.counterpart_name !== null) return item.counterpart_name
  return item.counterpart_user_id === null ? '对方' : '一位家人'
}

async function respond(item: PendingInvitation, action: 'accept' | 'reject'): Promise<void> {
  actingId.value = item.id
  feedback.value = null
  try {
    await spaces.resolveInvitation(item.id, action)
    feedbackError.value = false
    feedback.value =
      action === 'accept' ? `已加入「${item.space_name}」` : `已拒绝「${item.space_name}」的邀请`
  } catch (cause) {
    feedbackError.value = true
    feedback.value = describeError(cause)
  } finally {
    actingId.value = null
  }
}

async function withdraw(item: PendingInvitation): Promise<void> {
  actingId.value = item.id
  feedback.value = null
  try {
    await spaces.withdrawInvitation(item.id)
    feedbackError.value = false
    feedback.value = `已撤回对「${item.space_name}」的申请`
  } catch (cause) {
    feedbackError.value = true
    feedback.value = describeError(cause)
  } finally {
    actingId.value = null
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="invitations-view" data-test="invitations-view">
    <div class="exit-row">
      <button class="back-link" type="button" data-test="invitations-back" @click="goHome">
        ← 返回我的家庭
      </button>
      <NButton size="small" secondary data-test="invitations-refresh" @click="load">
        刷新
      </NButton>
    </div>

    <article class="invitations-hero">
      <header class="hero-head">
        <div class="hero-identity">
          <p class="hero-kind">家庭空间</p>
          <h1 class="hero-title">邀请与申请</h1>
          <p class="hero-subtitle">
            这里列出所有发给你的空间邀请，以及你发起的加入申请；与当前所在空间无关。
            邀请需要房主先批准、再由你接受；你发起的申请由对方空间的房主批准后生效。
          </p>
        </div>
      </header>

      <p
        v-if="feedback !== null"
        class="feedback"
        :class="{ 'feedback--error': feedbackError }"
        data-test="invitations-feedback"
      >
        {{ feedback }}
      </p>

      <NSpin v-if="loading && spaces.invitations.length === 0" :show="true" class="loading-spin" />

      <section v-else-if="loadError !== null" class="status-panel" data-test="invitations-error">
        <h2 class="status-title">{{ loadError.title }}</h2>
        <p class="status-text">{{ loadError.text }}</p>
        <NButton size="small" data-test="invitations-retry" @click="load">重新加载</NButton>
      </section>

      <template v-else>
        <!-- 分区 1：待我处理（别人邀请我） -->
        <section class="invite-section" data-test="section-incoming">
          <h2 class="section-title">待我处理</h2>
          <p class="section-hint">房主已批准的邀请可以直接接受；还在等房主批准时只能等待。</p>
          <NEmpty
            v-if="incoming.length === 0"
            description="没有等待你处理的邀请"
            size="small"
            data-test="incoming-empty"
          />
          <ul v-else class="invite-list">
            <li
              v-for="item in incoming"
              :key="item.id"
              class="invite-item"
              data-test="incoming-item"
            >
              <div class="invite-main">
                <p class="invite-title">
                  <strong>{{ item.space_name }}</strong>
                  <span class="fg-badge fg-badge--neutral">{{
                    item.space_kind === 'lineage' ? '族谱空间' : '家庭空间'
                  }}</span>
                  <span
                    class="fg-badge"
                    :class="item.stage === 'awaiting_me' ? 'fg-badge--accent' : 'fg-badge--proposed'"
                    data-test="incoming-stage"
                  >
                    {{ stageLabel(item) }}
                  </span>
                </p>
                <p class="invite-meta" data-test="incoming-meta">
                  <span>来自：{{ counterpartText(item) }}</span>
                  <span v-if="item.relation_label !== null">· 关系：{{ item.relation_label }}</span>
                </p>
              </div>
              <div class="invite-actions">
                <NButton
                  size="small"
                  type="primary"
                  secondary
                  :loading="actingId === item.id"
                  :disabled="!canRespond(item)"
                  :data-test="`invitation-accept-${item.id}`"
                  @click="respond(item, 'accept')"
                >
                  接受
                </NButton>
                <NButton
                  size="small"
                  secondary
                  :loading="actingId === item.id"
                  :disabled="!canRespond(item)"
                  :data-test="`invitation-reject-${item.id}`"
                  @click="respond(item, 'reject')"
                >
                  拒绝
                </NButton>
              </div>
            </li>
          </ul>
        </section>

        <!-- 分区 2：我发起的申请（只读进度 + 撤回） -->
        <section class="invite-section" data-test="section-outgoing">
          <h2 class="section-title">我发起的申请</h2>
          <p class="section-hint">申请与邀请码兑换都由对方空间的房主批准后生效。</p>
          <NEmpty
            v-if="outgoing.length === 0"
            description="没有正在处理的申请"
            size="small"
            data-test="outgoing-empty"
          />
          <ul v-else class="invite-list">
            <li v-for="item in outgoing" :key="item.id" class="invite-item" data-test="outgoing-item">
              <div class="invite-main">
                <p class="invite-title">
                  <strong>{{ item.space_name }}</strong>
                  <span class="fg-badge fg-badge--neutral">{{
                    item.space_kind === 'lineage' ? '族谱空间' : '家庭空间'
                  }}</span>
                  <span class="fg-badge fg-badge--proposed" data-test="outgoing-stage">
                    {{ stageLabel(item) }}
                  </span>
                </p>
                <p class="invite-meta">
                  <span>对方：{{ counterpartText(item) }}</span>
                  <span v-if="item.relation_label !== null">· 关系：{{ item.relation_label }}</span>
                </p>
              </div>
              <div class="invite-actions">
                <NButton
                  size="small"
                  secondary
                  :loading="actingId === item.id"
                  :data-test="`invitation-withdraw-${item.id}`"
                  @click="withdraw(item)"
                >
                  撤回
                </NButton>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </article>
  </main>
</template>

<style scoped>
.invitations-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 1120px;
  margin: 0 auto;
  padding: 32px 44px 48px;
  box-sizing: border-box;
}

.exit-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

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

.back-link:hover {
  color: var(--fg-accent);
}

.invitations-hero {
  display: flex;
  flex-direction: column;
  padding: 36px 40px 28px;
  box-sizing: border-box;
  background:
    linear-gradient(125deg, color-mix(in srgb, var(--fg-ink) 9%, transparent), transparent 54%),
    var(--fg-glass-surface);
  border: 1px solid var(--fg-glass-border);
  border-top-color: color-mix(in srgb, var(--fg-ink) 30%, transparent);
  border-radius: 8px;
  backdrop-filter: blur(28px) saturate(115%);
  -webkit-backdrop-filter: blur(28px) saturate(115%);
  box-shadow: var(--fg-shadow-raised);
}

@supports not (backdrop-filter: blur(28px)) {
  .invitations-hero {
    background: var(--fg-surface-raised);
  }
}

.hero-head {
  padding-bottom: 24px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.hero-kind {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.hero-title {
  margin: 0;
  color: var(--fg-ink);
  font-family: var(--fg-font-display);
  font-size: 32px;
  line-height: 1.4;
  font-weight: 600;
}

.hero-subtitle {
  max-width: 720px;
  margin: 6px 0 0;
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.7;
}

.loading-spin {
  min-height: 160px;
}

.feedback {
  margin: 14px 0 0;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.feedback--error {
  color: var(--fg-danger, #c0392b);
}

.status-panel,
.invite-section {
  padding: 14px 0 12px;
  border-bottom: 1px solid var(--fg-glass-border);
}

.invite-section:last-child {
  border-bottom: 0;
}

.status-panel :deep(.n-empty),
.invite-section :deep(.n-empty) {
  --n-icon-size: 30px;
  --n-text-color: var(--fg-ink-faint);
  padding: 2px 0 0;
  min-height: 0;
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

.invite-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.invite-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  background: var(--fg-glass-surface-raised);
  min-height: 44px;
  box-sizing: border-box;
}

.invite-main {
  min-width: 0;
  flex: 1;
}

.invite-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  margin: 0;
  color: var(--fg-ink);
  font-size: 14px;
}

.invite-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin: 6px 0 0;
  color: var(--fg-ink-faint);
  font-size: 11px;
}

.invite-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 8px;
  align-self: center;
}

@media (max-width: 600px) {
  .invitations-view {
    padding: 22px 16px 36px;
  }

  .invitations-hero {
    padding: 24px 20px 20px;
  }

  .invite-item {
    flex-direction: column;
  }

  .invite-actions {
    align-self: flex-start;
  }

  .invitations-view :deep(.n-button--small-type) {
    min-height: 44px;
  }
}
</style>
