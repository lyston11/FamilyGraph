<script setup lang="ts">
// 当前空间管理（design.md §5.5，09-01 Phase 6；09-06 增模型设置分区）：
// - 六分区侧栏（桌面侧栏 / 移动端顶部标签）：概览、成员、邀请与申请、Bridge 通知、模型设置、空间设置；
// - 入口只由当前 space 的 active space_admin 关系决定（router 守卫 fail-closed 已拦截
//   越权直达），页面内对非管理员仍渲染安全拒绝态（双保险）；
// - 成员/邀请/申请全部复用既有流程组件与 spaces store 命令（操作后服务端 reload），
//   不新增直接编辑 SourceFact / SpaceMember / PersonalFamilyView 的前端路径；
// - Bridge 通知分区只读：仅安全状态与通知时间，无 approve/reject/consent/revoke 控件
//   （管理员对跨 LineageSpace bridge 只有通知查看权，PRD §2.6）；
// - 空间设置只有既有 PATCH /spaces/{space_id} 合同（空间名），无新增授权字段；
// - 模型设置分区：assistant/steward 双 Agent 模型选择与云同意（09-06 治理迁移，
//   SpaceModelSettingsPanel 自管数据，权限同 PATCH /spaces 的 space_admin 语义）。
// - 分区深链：?section= 合法 key 直达对应分区，切 tab 时 replace 写回（09-06 R5）。
import { NAlert, NButton, NEmpty, NInput, NSpin, useMessage } from 'naive-ui'
import { computed, onMounted, ref, watch, type InputHTMLAttributes } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/errors'
import ActionCardInbox from '@/components/actioncard/ActionCardInbox.vue'
import InviteMemberDialog from '@/components/member/InviteMemberDialog.vue'
import PendingProfileRefs from '@/components/member/PendingProfileRefs.vue'
import SpaceGovernancePanel from '@/components/member/SpaceGovernancePanel.vue'
import SpaceManagerApplicationPanel from '@/components/member/SpaceManagerApplicationPanel.vue'
import SpaceModelSettingsPanel from '@/components/member/SpaceModelSettingsPanel.vue'
import NoticeItemRow from '@/components/notifications/NoticeItemRow.vue'
import { useNotificationsStore } from '@/stores/notifications'
import { useSpacesStore } from '@/stores/spaces'
import type { NotificationItem } from '@/types/api'

type ManagementSection = 'overview' | 'members' | 'invites' | 'bridge' | 'models' | 'settings'

const SECTIONS: ReadonlyArray<{ key: ManagementSection; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'members', label: '成员' },
  { key: 'invites', label: '邀请与申请' },
  { key: 'bridge', label: 'Bridge 通知' },
  { key: 'models', label: '模型设置' },
  { key: 'settings', label: '空间设置' },
]

const route = useRoute()
const router = useRouter()
const spaces = useSpacesStore()
const notifications = useNotificationsStore()
const message = useMessage()

// ---- 分区状态：R5 section 深链同步（挂载时读 ?section=，切 tab 写回，URL 可分享）----
const SECTION_KEYS: ReadonlySet<string> = new Set(SECTIONS.map((section) => section.key))

/** 仅合法 key 生效（query 可能是数组等非法形态），非法回退概览 */
function parseSection(raw: unknown): ManagementSection {
  return typeof raw === 'string' && SECTION_KEYS.has(raw) ? (raw as ManagementSection) : 'overview'
}

const activeSection = ref<ManagementSection>(parseSection(route.query.section))

watch(
  () => route.query.section,
  (value) => {
    activeSection.value = parseSection(value)
  },
)

function selectSection(section: ManagementSection): void {
  activeSection.value = section
  // replace 不产生历史噪音；保留其他 query 参数
  void router.replace({ query: { ...route.query, section } })
}

const loading = computed(() => spaces.loading)
const targetSpaceId = computed(() => Number(route.params.spaceId))
const space = computed(() => spaces.spaces.find((item) => item.id === targetSpaceId.value) ?? null)
const hasAccess = computed(() => spaces.currentSpaceId === targetSpaceId.value && spaces.canManageSpace)
const roleLabel = computed(() =>
  spaces.currentRole === 'space_admin' ? '空间管理员' : '无空间管理权限',
)
const pendingCount = computed(() => spaces.members.filter((member) => member.status === 'pending').length)

onMounted(async () => {
  await spaces.loadMembers(targetSpaceId.value).catch(() => undefined)
  // AppShell 已按空间预取通知；此处仅在无缓存时补拉一次（带 ETag，304 复用快照）
  if (targetSpaceId.value > 0 && notifications.forSpace(targetSpaceId.value) === null) {
    void notifications.load(targetSpaceId.value).catch(() => undefined)
  }
})

function goFamilySpace(): void {
  void router.push({ name: 'family-space' })
}

// ---- 邀请与申请：复用既有 InviteMemberDialog / ActionCardInbox / 申请面板 ----
const inviteOpen = ref(false)
const inboxOpened = ref(false)

// ---- Bridge 通知（只读）：notifications store 中 bridge 类通知，按当前空间读取 ----
const bridgeItems = computed<NotificationItem[]>(() => {
  const spaceId = targetSpaceId.value
  if (!Number.isInteger(spaceId)) return []
  return (notifications.forSpace(spaceId)?.items ?? []).filter((item) => item.kind === 'bridge')
})
const bridgeLoading = computed(() => notifications.isLoading(targetSpaceId.value))
const bridgeError = computed(() => notifications.errorFor(targetSpaceId.value))
/** 通知端点运行时 404（BLOCKER 占位）→ 安静的合同未就绪态，与通知页同一口径 */
const bridgeContractUnready = computed(
  () => bridgeError.value instanceof ApiError && bridgeError.value.status === 404,
)

// ---- 空间设置：既有 PATCH 合同，仅空间名一个字段 ----
const nameDraft = ref('')
const savingName = ref(false)
const nameInputProps = {
  'data-test': 'space-name-input',
  'aria-label': '空间名称',
} as InputHTMLAttributes

watch(activeSection, (section) => {
  if (section === 'settings') nameDraft.value = space.value?.name ?? ''
})

const nameDirty = computed(() => nameDraft.value.trim() !== (space.value?.name ?? ''))

async function saveName(): Promise<void> {
  const name = nameDraft.value.trim()
  if (!space.value || !name || name.length > 64) return
  savingName.value = true
  try {
    const updated = await spaces.rename(space.value.id, name)
    nameDraft.value = updated.name
    message.success('空间名称已更新')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    savingName.value = false
  }
}
</script>

<template>
  <main class="management-view" data-test="space-management-view">
    <header class="management-header">
      <div>
        <p class="eyebrow">空间治理</p>
        <h1>家庭空间管理</h1>
        <p class="description">仅当前空间的管理员可以管理本空间的成员、邀请与基本设置。</p>
      </div>
      <NButton data-test="management-back" @click="goFamilySpace">返回家族树</NButton>
    </header>

    <NSpin :show="loading">
      <!-- 双保险：守卫已 fail-closed 拦截越权直达，页面内非管理员仍渲染安全拒绝态 -->
      <NAlert v-if="!hasAccess || !space" type="warning" :show-icon="true" data-test="management-denied">
        当前账号没有管理这个家庭空间的权限。
        <NButton size="small" secondary class="inline-action" @click="goFamilySpace">回到家族树</NButton>
      </NAlert>
      <div v-else class="management-layout">
        <nav class="management-nav" aria-label="空间管理分区" data-test="management-nav">
          <button
            v-for="section in SECTIONS"
            :key="section.key"
            type="button"
            class="management-tab"
            :class="{ 'management-tab--active': activeSection === section.key }"
            :aria-pressed="activeSection === section.key"
            :data-test="`management-tab-${section.key}`"
            @click="selectSection(section.key)"
          >
            {{ section.label }}
          </button>
        </nav>

        <div class="management-content">
          <!-- 概览：空间名称 / 类型 / 成员概要 / 管理员标记（全部来自 spaces/members store） -->
          <section v-if="activeSection === 'overview'" class="section-card" data-test="section-overview">
            <h2 class="section-title">概览</h2>
            <div class="space-overview">
              <div>
                <span class="label">空间名称</span>
                <strong data-test="management-space-name">{{ space.name }}</strong>
              </div>
              <div>
                <span class="label">空间类型</span>
                <span
                  class="fg-badge"
                  :class="space.kind === 'lineage' ? 'fg-badge--accent' : 'fg-badge--confirmed'"
                  data-test="management-space-kind"
                >
                  {{ space.kind === 'lineage' ? '族谱空间' : '家庭空间' }}
                </span>
              </div>
              <div>
                <span class="label">当前角色</span>
                <span class="fg-badge fg-badge--accent" data-test="management-current-role">{{ roleLabel }}</span>
              </div>
              <div>
                <span class="label">成员 / 待处理</span>
                <strong data-test="management-counts">{{ space.member_count }} / {{ pendingCount }}</strong>
              </div>
            </div>
            <p class="section-hint">成员概要来自服务端成员关系投影；授权只按当前空间的成员关系判定。</p>
          </section>

          <!-- 成员：既有 SpaceMemberOut 列表 + 邀请/撤回/移除/交接（spaces store 既有命令，操作后 reload） -->
          <section v-else-if="activeSection === 'members'" class="section-card" data-test="section-members">
            <h2 class="section-title">成员</h2>
            <p class="section-hint">
              成员列表与移除、交接走既有空间治理流程；操作完成后由服务端重新载入成员关系。
            </p>
            <SpaceGovernancePanel />
            <div class="pending-refs-block">
              <PendingProfileRefs :refs="spaces.profileRefs" />
              <NEmpty
                v-if="spaces.profileRefs.length === 0"
                description="当前空间没有待确档引用"
                size="small"
              />
            </div>
          </section>

          <!-- 邀请与申请：既有 InviteMemberDialog / ActionCard 待办 / 管理员申请面板 -->
          <section v-else-if="activeSection === 'invites'" class="section-card" data-test="section-invites">
            <h2 class="section-title">邀请与申请</h2>
            <div class="invite-entry">
              <NButton
                v-if="spaces.canInvite"
                type="primary"
                secondary
                data-test="open-invite-dialog"
                @click="inviteOpen = true"
              >
                邀请成员
              </NButton>
              <span class="section-hint">受邀人本人接受后才成为 active 成员；邀请不需要平台审批。</span>
            </div>
            <div class="panel-block">
              <h3 class="block-title">待办（管家建议）</h3>
              <ActionCardInbox v-model:opened="inboxOpened" />
            </div>
            <div class="panel-block">
              <h3 class="block-title">管理员申请与交接</h3>
              <SpaceManagerApplicationPanel />
            </div>
          </section>

          <!-- Bridge 通知（只读）：安全状态与时间，无任何操作控件 -->
          <section v-else-if="activeSection === 'bridge'" class="section-card" data-test="section-bridge">
            <h2 class="section-title">Bridge 通知</h2>
            <p class="section-hint">
              跨家族连接（Bridge）通知对空间管理员只读：仅展示安全状态与通知时间。
              管理员没有批准、否决、修改或撤销权，Bridge 生效只由相关用户本人双向同意决定。
            </p>
            <NSpin v-if="bridgeLoading && bridgeItems.length === 0" :show="true" size="small" />
            <p v-else-if="bridgeContractUnready" class="safe-note" data-test="bridge-contract-unready">
              通知服务合同未就绪，已按安全策略不展示任何数据。
            </p>
            <p v-else-if="bridgeError !== null" class="safe-note" data-test="bridge-notice-error">
              Bridge 通知暂时无法加载，稍后可在通知中心查看。
            </p>
            <NEmpty
              v-else-if="bridgeItems.length === 0"
              description="当前空间没有 Bridge 通知"
              size="small"
              data-test="bridge-notices-empty"
            />
            <ul v-else class="bridge-list" data-test="bridge-notice-list">
              <li v-for="item in bridgeItems" :key="item.id" class="bridge-item" data-test="bridge-notice-item">
                <NoticeItemRow :item="item" />
              </li>
            </ul>
          </section>

          <!-- 模型设置（09-06 治理迁移）：assistant/steward 双 Agent 模型选择与云同意 -->
          <section v-else-if="activeSection === 'models'" class="section-card" data-test="section-models">
            <h2 class="section-title">模型设置</h2>
            <p class="section-hint">
              为助手与管家分别选择平台管理员允许的模型；云端执行需显式同意，随时可恢复平台默认或停用。
            </p>
            <SpaceModelSettingsPanel :space-id="space.id" />
          </section>

          <!-- 空间设置：既有 PATCH /spaces/{space_id}，仅空间名，无新增授权字段 -->
          <section v-else-if="activeSection === 'settings'" class="section-card" data-test="section-settings">
            <h2 class="section-title">空间设置</h2>
            <form class="settings-form" @submit.prevent="saveName">
              <label class="settings-field">
                <span class="label">空间名称</span>
                <NInput
                  v-model:value="nameDraft"
                  :maxlength="64"
                  :input-props="nameInputProps"
                  placeholder="空间名称"
                />
              </label>
              <NButton
                attr-type="submit"
                type="primary"
                :loading="savingName"
                :disabled="!nameDirty"
                data-test="space-name-save"
              >
                保存
              </NButton>
            </form>
            <p class="section-hint">
              空间设置仅支持修改空间名称；成员、角色与授权由服务端成员关系判定，不在此配置。
            </p>
          </section>
        </div>
      </div>
    </NSpin>

    <InviteMemberDialog :visible="inviteOpen" @update:visible="inviteOpen = $event" />
  </main>
</template>

<style scoped>
.management-view { max-width: 1120px; margin: 0 auto; padding: 32px 20px 48px; box-sizing: border-box; }
.management-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 5px; color: var(--fg-accent); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0 0 8px; color: var(--fg-ink); font-family: var(--fg-font-display); font-size: 30px; }
.description { margin: 0; color: var(--fg-ink-secondary); font-size: 14px; }

.management-layout { display: flex; gap: 20px; align-items: flex-start; }

/* 桌面侧栏 */
.management-nav { display: flex; flex-direction: column; gap: 4px; flex: 0 0 168px; position: sticky; top: 76px; }
.management-tab {
  display: flex; align-items: center; min-height: 44px; box-sizing: border-box;
  padding: 8px 14px; border: 1px solid transparent; border-radius: var(--fg-radius-control);
  background: none; color: var(--fg-ink-secondary); font-size: 14px; text-align: left; cursor: pointer;
}
.management-tab:hover { color: var(--fg-ink); background: var(--fg-surface-sunken); }
.management-tab--active { color: var(--fg-accent); background: var(--fg-accent-soft); font-weight: 600; }

.management-content { min-width: 0; flex: 1; }
.section-card {
  padding: 24px;
  background: var(--fg-glass-surface);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.5);
  box-shadow:
    0 4px 20px color-mix(in srgb, var(--fg-ink) 6%, transparent),
    inset 0 1px 0 color-mix(in srgb, var(--fg-surface-raised) 20%, transparent);
}
.section-title { margin: 0 0 6px; font-size: 17px; color: var(--fg-ink); }
.section-hint { margin: 0 0 12px; color: var(--fg-ink-secondary); font-size: 12px; line-height: 1.6; }

.space-overview { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.space-overview > div {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 16px;
  background: var(--fg-glass-surface-raised);
  backdrop-filter: blur(16px) saturate(180%);
  -webkit-backdrop-filter: blur(16px) saturate(180%);
  border: 1px solid var(--fg-glass-border);
  border-radius: calc(var(--fg-radius-card) * 1.2);
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.space-overview > div:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--fg-accent) 40%, var(--fg-glass-border));
  box-shadow: 0 6px 20px color-mix(in srgb, var(--fg-ink) 8%, transparent), 0 0 12px var(--fg-glass-glow);
}
.label { color: var(--fg-ink-secondary); font-size: 12px; }
.space-overview strong { color: var(--fg-ink); font-size: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inline-action { margin-left: 8px; }

.pending-refs-block { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--fg-line); }

.invite-entry { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
.invite-entry .section-hint { margin: 0; }
.panel-block { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--fg-line); }
.panel-block:first-of-type { border-top: none; margin-top: 4px; padding-top: 0; }
.block-title { margin: 0 0 10px; font-size: 14px; color: var(--fg-ink); }

.bridge-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.bridge-item { padding: 12px; border: 1px solid var(--fg-line-strong); border-radius: var(--fg-radius-card); background: var(--fg-surface); }
.safe-note { margin: 0; color: var(--fg-ink-secondary); font-size: 13px; line-height: 1.6; }

.settings-form { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.settings-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1 1 260px; max-width: 360px; }

@media (max-width: 768px) {
  .management-header { flex-direction: column; }
  .space-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  /* 移动端顶部标签：侧栏变水平分段条 */
  .management-layout { flex-direction: column; }
  .management-nav {
    position: static; flex-direction: row; flex-wrap: nowrap; overflow-x: auto;
    flex: 0 0 auto; width: 100%; box-sizing: border-box; padding-bottom: 4px;
  }
  .management-tab { flex: 0 0 auto; white-space: nowrap; }
}
@media (max-width: 420px) {
  .management-view { padding: 22px 12px 36px; }
  .space-overview { grid-template-columns: 1fr 1fr; }
  h1 { font-size: 24px; }
}
</style>
