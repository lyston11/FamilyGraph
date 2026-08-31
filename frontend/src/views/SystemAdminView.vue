<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError } from '@/api/errors'
import {
  decideManagerApplication,
  fetchAdminAccounts,
  fetchAdminSpaceMembers,
  fetchAdminSpaces,
  fetchAdminSpaceManagers,
  fetchAdminTransferConsents,
  fetchManagerApplications,
  type AdminAccountMetadata,
  type AdminSpaceManagerMetadata,
  type AdminSpaceMemberMetadata,
  type AdminSpaceMetadata,
  type AdminTransferConsentMetadata,
} from '@/api/admin'
import type { SpaceManagerApplication } from '@/types/api'

/**
 * 系统管理员后台（PRD R5）。
 *
 * 这里只投影运营所需的最小元数据：账号、空间、管理员归属、申请与工单状态。
 * 任何家庭内容（人物、关系、记忆）都不经此页；空间成员表也只显示姓名与
 * 角色/状态，用于核对「每空间一个管理员」这条不变量。
 *
 * 审批是两阶段的：第一次 approve 只建立原管理员同意工单，申请仍是 pending；
 * 等原管理员本人同意后再次 approve 才真正换人。页面据 transfer_consent_status
 * 明示当前卡在哪一步，避免运营以为「点过了就生效了」。
 */
const accounts = ref<AdminAccountMetadata[]>([])
const spaces = ref<AdminSpaceMetadata[]>([])
const managers = ref<AdminSpaceManagerMetadata[]>([])
const applications = ref<SpaceManagerApplication[]>([])
const consents = ref<AdminTransferConsentMetadata[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const notice = ref<string | null>(null)

const decisionNote = ref<Record<number, string>>({})
const decidingId = ref<number | null>(null)

// 展开某个空间才拉成员：默认不批量抓取全站成员构成。
const expandedSpaceId = ref<number | null>(null)
const spaceMembers = ref<AdminSpaceMemberMetadata[]>([])
const membersLoading = ref(false)

const pendingApplications = computed(() =>
  applications.value.filter((app) => app.status === 'pending'),
)
const pendingConsents = computed(() => consents.value.filter((c) => c.status === 'pending'))

async function loadAll(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [accountRows, spaceRows, managerRows, applicationRows, consentRows] = await Promise.all([
      fetchAdminAccounts(),
      fetchAdminSpaces(),
      fetchAdminSpaceManagers(),
      fetchManagerApplications(),
      fetchAdminTransferConsents(),
    ])
    accounts.value = accountRows
    spaces.value = spaceRows
    managers.value = managerRows
    applications.value = applicationRows
    consents.value = consentRows
  } catch {
    error.value = '无法加载系统治理数据'
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)

/** 申请对应的工单状态：优先用申请自带投影，其次回落到工单列表 */
function consentFor(app: SpaceManagerApplication): AdminTransferConsentMetadata | null {
  return consents.value.find((c) => c.application_id === app.id) ?? null
}

function applicationStage(app: SpaceManagerApplication): string {
  if (app.status === 'approved') return '已完成交接'
  if (app.status === 'rejected') return '已驳回'
  const consentStatus = app.transfer_consent_status ?? consentFor(app)?.status ?? null
  if (consentStatus === 'pending') return '待原管理员同意'
  if (consentStatus === 'accepted') return '原管理员已同意，可完成交接'
  if (consentStatus === 'rejected') return '原管理员已谢绝'
  if (consentStatus === 'expired') return '同意已失效，需重新征询'
  return '待系统管理员受理'
}

async function decide(app: SpaceManagerApplication, decision: 'approve' | 'reject'): Promise<void> {
  const note = (decisionNote.value[app.id] ?? '').trim()
  if (decision === 'reject' && !note) {
    notice.value = '驳回必须填写理由'
    return
  }
  decidingId.value = app.id
  notice.value = null
  try {
    await decideManagerApplication(app.id, decision, note || undefined)
    notice.value =
      decision === 'reject'
        ? '已驳回该申请'
        : '已受理。若原管理员尚未同意，申请会停在「待原管理员同意」，同意后再受理一次即可完成交接。'
    delete decisionNote.value[app.id]
    await loadAll()
  } catch (err) {
    notice.value = err instanceof ApiError ? err.message : '裁决失败，请稍后重试'
  } finally {
    decidingId.value = null
  }
}

async function toggleSpaceMembers(spaceId: number): Promise<void> {
  if (expandedSpaceId.value === spaceId) {
    expandedSpaceId.value = null
    spaceMembers.value = []
    return
  }
  expandedSpaceId.value = spaceId
  membersLoading.value = true
  try {
    spaceMembers.value = await fetchAdminSpaceMembers(spaceId)
  } catch {
    spaceMembers.value = []
  } finally {
    membersLoading.value = false
  }
}

function roleLabel(role: string): string {
  if (role === 'space_admin') return '空间管理员'
  if (role === 'member') return '成员'
  if (role === 'guest') return '访客'
  return role
}

function kindLabel(kind: string): string {
  return kind === 'lineage' ? '族谱空间' : '家庭空间'
}
</script>

<template>
  <div class="system-admin-page">
    <header class="page-heading">
      <p class="eyebrow">SYSTEM GOVERNANCE</p>
      <h1>系统管理员后台</h1>
      <p>仅显示账号、成员关系、管理员归属和空间元数据；不含任何家庭内容。</p>
    </header>

    <p v-if="loading" data-test="system-admin-loading">正在加载治理数据...</p>
    <p v-else-if="error" role="alert" data-test="system-admin-error">{{ error }}</p>
    <template v-else>
      <p v-if="notice" class="notice" role="status" data-test="system-admin-notice">{{ notice }}</p>

      <section aria-labelledby="application-heading">
        <h2 id="application-heading">
          管理员申请审批
          <span class="count" data-test="pending-application-count">待办 {{ pendingApplications.length }}</span>
        </h2>
        <p v-if="pendingApplications.length === 0" class="empty" data-test="no-pending-applications">
          当前没有待审批的管理员申请。
        </p>
        <ul v-else class="application-list">
          <li
            v-for="app in pendingApplications"
            :key="app.id"
            class="application-card"
            :data-test="`application-${app.id}`"
          >
            <div class="application-head">
              <span>
                {{ app.applicant_name ?? `用户 #${app.applicant_user_id}` }}
                申请接手「{{ app.space_name ?? `空间 #${app.space_id}` }}」
              </span>
              <span class="fg-badge fg-badge--proposed" :data-test="`application-stage-${app.id}`">
                {{ applicationStage(app) }}
              </span>
            </div>
            <p class="application-meta">
              现任管理员：{{ app.current_manager_name ?? consentFor(app)?.current_manager_name ?? '未知' }}
              <template v-if="app.space_kind">· {{ kindLabel(app.space_kind) }}</template>
            </p>
            <div class="application-actions">
              <input
                v-model="decisionNote[app.id]"
                class="note-input"
                placeholder="裁决备注（驳回必填）"
                :aria-label="`第 ${app.id} 号申请的裁决备注`"
                :data-test="`decision-note-${app.id}`"
              />
              <button
                type="button"
                class="btn btn--primary"
                :disabled="decidingId === app.id"
                :data-test="`approve-${app.id}`"
                @click="decide(app, 'approve')"
              >
                受理 / 完成交接
              </button>
              <button
                type="button"
                class="btn"
                :disabled="decidingId === app.id"
                :data-test="`reject-${app.id}`"
                @click="decide(app, 'reject')"
              >
                驳回
              </button>
            </div>
          </li>
        </ul>
      </section>

      <section aria-labelledby="consent-heading">
        <h2 id="consent-heading">
          原管理员同意工单
          <span class="count" data-test="pending-consent-count">待回应 {{ pendingConsents.length }}</span>
        </h2>
        <p v-if="consents.length === 0" class="empty" data-test="no-consents">暂无交接工单。</p>
        <table v-else data-test="consent-table">
          <thead>
            <tr><th>目标空间</th><th>申请人</th><th>现任管理员</th><th>状态</th><th>回应理由</th></tr>
          </thead>
          <tbody>
            <tr v-for="consent in consents" :key="consent.id" :data-test="`consent-${consent.id}`">
              <td>{{ consent.space_name }}</td>
              <td>{{ consent.applicant_name }}</td>
              <td>{{ consent.current_manager_name }}</td>
              <td>{{ consent.status }}</td>
              <td>{{ consent.response_reason || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section aria-labelledby="space-heading">
        <h2 id="space-heading">空间元数据</h2>
        <table>
          <thead><tr><th>空间</th><th>类型</th><th>状态</th><th>空间管理员</th><th>成员</th></tr></thead>
          <tbody>
            <template v-for="space in spaces" :key="space.id">
              <tr>
                <td>{{ space.name }}</td>
                <td>{{ kindLabel(space.kind) }}</td>
                <td>{{ space.status }}</td>
                <td>{{ space.manager_name || '待修复' }}</td>
                <td>
                  <button
                    type="button"
                    class="btn btn--link"
                    :aria-expanded="expandedSpaceId === space.id"
                    :data-test="`toggle-space-members-${space.id}`"
                    @click="toggleSpaceMembers(space.id)"
                  >
                    {{ expandedSpaceId === space.id ? '收起' : '查看成员' }}
                  </button>
                </td>
              </tr>
              <tr v-if="expandedSpaceId === space.id" :data-test="`space-members-${space.id}`">
                <td colspan="5">
                  <p v-if="membersLoading">正在加载成员...</p>
                  <p v-else-if="spaceMembers.length === 0">没有可显示的成员元数据。</p>
                  <ul v-else class="member-list">
                    <li v-for="member in spaceMembers" :key="member.user_id">
                      {{ member.name }} · {{ roleLabel(member.role) }} · {{ member.status }}
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </section>

      <section aria-labelledby="manager-heading">
        <h2 id="manager-heading">管理员归属</h2>
        <table>
          <thead><tr><th>管理员</th><th>管理空间</th><th>空间类型</th></tr></thead>
          <tbody>
            <tr v-for="manager in managers" :key="`${manager.space_id}-${manager.manager_user_id}`">
              <td>{{ manager.manager_name }}</td>
              <td>{{ manager.space_name }}</td>
              <td>{{ kindLabel(manager.space_kind) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section aria-labelledby="account-heading">
        <h2 id="account-heading">系统账号</h2>
        <table>
          <thead><tr><th>主体</th><th>类型</th><th>状态</th><th>锁定至</th></tr></thead>
          <tbody>
            <tr v-for="account in accounts" :key="account.account_id">
              <td>{{ account.subject_id }}</td>
              <td>{{ account.subject_type === 'system_admin' ? '系统管理员' : '家庭用户' }}</td>
              <td>{{ account.status }}</td>
              <td>{{ account.locked_until || '未锁定' }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>

<style scoped>
.system-admin-page { max-width: 1100px; margin: 0 auto; padding: 40px 24px 72px; color: var(--fg-ink); }
.page-heading { margin-bottom: 32px; }
.eyebrow { color: var(--fg-accent); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
h1 { margin: 6px 0 8px; font-family: var(--fg-font-display); font-size: 34px; }
h2 { display: flex; align-items: center; gap: 10px; margin: 28px 0 12px; font-size: 18px; }
.count { color: var(--fg-ink-secondary); font-size: 12px; font-weight: 400; }
.notice { padding: 10px 12px; background: color-mix(in srgb, var(--fg-status-proposed) 10%, transparent); border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent); border-radius: var(--fg-radius-control); font-size: 13px; }
.empty { color: var(--fg-ink-secondary); font-size: 13px; }
table { width: 100%; border-collapse: collapse; background: var(--fg-surface-raised); }
th, td { padding: 12px 14px; border-bottom: 1px solid var(--fg-line); text-align: left; font-size: 13px; }
th { color: var(--fg-ink-secondary); font-weight: 600; }
.application-list, .member-list { list-style: none; margin: 0; padding: 0; }
.application-list { display: flex; flex-direction: column; gap: 12px; }
.application-card { display: flex; flex-direction: column; gap: 8px; padding: 14px; background: var(--fg-surface-raised); border: 1px solid var(--fg-line); border-radius: var(--fg-radius-card); }
.application-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 14px; }
.application-meta { margin: 0; color: var(--fg-ink-secondary); font-size: 12px; }
.application-actions { display: flex; align-items: center; gap: 8px; }
.note-input { flex: 1; min-width: 0; padding: 7px 10px; color: var(--fg-ink); background: var(--fg-surface); border: 1px solid var(--fg-line); border-radius: var(--fg-radius-control); font-size: 13px; }
.btn { padding: 7px 14px; color: var(--fg-ink); background: var(--fg-surface); border: 1px solid var(--fg-line); border-radius: var(--fg-radius-control); font-size: 13px; cursor: pointer; }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.btn--primary { color: var(--fg-surface); background: var(--fg-accent); border-color: var(--fg-accent); }
.btn--link { padding: 0; background: none; border: none; color: var(--fg-accent); text-decoration: underline; }
.member-list { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
@media (max-width: 640px) { .application-actions { flex-direction: column; align-items: stretch; } }
</style>
