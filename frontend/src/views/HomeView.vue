<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { VNodeChild } from 'vue'
import { NAlert, NBadge, NButton, NEmpty, NInput, NModal, NSelect, NSpin, useMessage } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import type { InputHTMLAttributes as VueInputHTMLAttributes } from 'vue'

import OneTimePinDialog from '@/components/member/OneTimePinDialog.vue'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import AddRelationDialog from '@/components/member/AddRelationDialog.vue'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { fetchMembersByPrefix } from '@/api/members'
import {
  fetchEligibleManagerTargets,
  fetchMyManagerApplications,
  fetchMyTransferConsents,
  respondTransferConsent,
  submitManagerApplication,
} from '@/api/spaces'
import { useMembersStore } from '@/stores/members'
import { useGraphStore } from '@/stores/graph'
import { useSpacesStore } from '@/stores/spaces'
import { ApiError } from '@/api/errors'
import type {
  EligibleManagerTarget,
  GenderType,
  ManagerTransferConsent,
  Member,
  SpaceManagerApplication,
  StructuredDate,
} from '@/types/api'

/**
 * M1 首页：「与我相关的档案」列表（自己 + 我创建的；admin 可见全部）。
 * m1d 后该列表被家庭空间画布取代（m1a design）。
 * 视觉（design.md §3.3）：空间切换器带类型图标（Household=共同生活 /
 * Lineage=谱系）；成员卡=横向立牌（姓字纸牌头像位 / 姓名 / 称谓或性别 / 生卒）
 * + 右侧 --fg-status-* 状态徽章 + 快捷动作。
 *
 * 空间管理权限（PRD R3/R4）：族谱空间管理员申请是「申请接手某个具体空间」，
 * 目标不是当前切换到的空间——候选目标、目标名称与是否已有在办申请全部由
 * 服务端 eligible-targets 裁定，前端不按本地 role 推断资格。审批要两步：
 * 系统管理员先受理，再由该空间原管理员在工单里同意，才会真正换人。
 * 邀请成员不走这条流程，active member（除 guest）可直接邀请。
 */
const auth = useAuthStore()
const members = useMembersStore()
const router = useRouter()
const message = useMessage()

const wizardOpen = ref(false)
// 一次性凭据：仅存在于本组件内存，弹窗关闭即清空（不可回看）
const issuedPin = ref('')
const issuedName = ref('')

onMounted(() => {
  members.load().catch(() => message.error('档案列表加载失败，请稍后重试'))
  // 空间与待处理邀请（AD-3）；收到的连接请求红点（审批 UI 归 m2c）
  spacesStore.load().catch(() => undefined)
  graphStore.loadIncoming().catch(() => undefined)
  loadMyApplications()
  loadEligibleTargets()
  loadMyConsents()
})

const relationDialogOpen = ref(false)
const graphStore = useGraphStore()
const spacesStore = useSpacesStore()

const createSpaceOpen = ref(false)
const newSpaceName = ref('')
const applyOpen = ref(false)
/** 正在提交的目标空间 id：按卡片粒度显示 loading，避免整个列表被锁住 */
const applyingSpaceId = ref<number | null>(null)
const myApplications = ref<SpaceManagerApplication[]>([])
const eligibleTargets = ref<EligibleManagerTarget[]>([])
const myConsents = ref<ManagerTransferConsent[]>([])
const consentReason = ref<Record<number, string>>({})
const respondingConsentId = ref<number | null>(null)

const spaceNameInputProps = {
  'data-test': 'space-name-input',
  'aria-label': '家庭空间名称',
} as VueInputHTMLAttributes

async function submitCreateSpace(): Promise<void> {
  if (!newSpaceName.value.trim()) return
  try {
    await spacesStore.create(newSpaceName.value.trim())
    newSpaceName.value = ''
    createSpaceOpen.value = false
  } catch {
    message.error('创建空间失败，请稍后重试')
  }
}
async function loadMyApplications(): Promise<void> {
  try {
    myApplications.value = await fetchMyManagerApplications()
  } catch {
    /* 状态行加载失败不阻塞首页 */
  }
}

/** 可申请目标由服务端裁定；失败时置空 → 入口隐藏（fail-closed） */
async function loadEligibleTargets(): Promise<void> {
  try {
    eligibleTargets.value = await fetchEligibleManagerTargets()
  } catch {
    eligibleTargets.value = []
  }
}

/** 我作为原管理员待处理的交接工单（PRD R3 第二阶段） */
async function loadMyConsents(): Promise<void> {
  try {
    myConsents.value = (await fetchMyTransferConsents()).filter((c) => c.status === 'pending')
  } catch {
    myConsents.value = []
  }
}

function openApplyDialog(): void {
  applyOpen.value = true
  void loadEligibleTargets()
}

async function applyForTarget(target: EligibleManagerTarget): Promise<void> {
  applyingSpaceId.value = target.space_id
  try {
    await submitManagerApplication('space_admin', { spaceId: target.space_id })
    message.success('申请已提交，等待系统管理员受理与原管理员同意')
    applyOpen.value = false
    await Promise.all([loadMyApplications(), loadEligibleTargets()])
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '提交失败，请稍后重试')
  } finally {
    applyingSpaceId.value = null
  }
}

async function respondConsent(
  consent: ManagerTransferConsent,
  decision: 'accept' | 'reject',
): Promise<void> {
  const reason = (consentReason.value[consent.id] ?? '').trim()
  if (decision === 'reject' && !reason) {
    message.error('谢绝交接需要填写理由')
    return
  }
  respondingConsentId.value = consent.id
  try {
    await respondTransferConsent(consent.id, decision, reason || undefined)
    message.success(
      decision === 'accept' ? '已同意交接，等待系统管理员完成换人' : '已谢绝该交接申请',
    )
    delete consentReason.value[consent.id]
    await Promise.all([loadMyConsents(), spacesStore.load().catch(() => undefined)])
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  } finally {
    respondingConsentId.value = null
  }
}

function consentText(consent: ManagerTransferConsent): string {
  const applicant = consent.applicant_name ?? `用户 #${consent.applicant_user_id ?? '?'}`
  return `${applicant} 申请接手「${consent.space_name ?? `空间 #${consent.space_id}`}」的管理员`
}

function applicationText(app: SpaceManagerApplication): string {
  return `「${app.space_name ?? '目标空间'}」空间管理员申请`
}

/**
 * 申请状态徽章：与全站领域状态同源（--fg-status-* / fg-badge）。
 * pending 且工单已建立时区分出「待原管理员同意」，让申请人知道卡在哪一步。
 */
function applicationBadge(app: SpaceManagerApplication): { text: string; cls: string } {
  if (app.status === 'approved') return { text: '已通过', cls: 'fg-badge fg-badge--confirmed' }
  if (app.status === 'rejected') return { text: '未通过', cls: 'fg-badge fg-badge--disputed' }
  if (app.transfer_consent_status === 'pending') {
    return { text: '待原管理员同意', cls: 'fg-badge fg-badge--proposed' }
  }
  return { text: '审批中', cls: 'fg-badge fg-badge--proposed' }
}

const inviteSearchInputProps = {
  'data-test': 'invite-search',
  'aria-label': '按名字前缀搜索成员',
} as VueInputHTMLAttributes

const inviteOpen = ref(false)
const inviteKeyword = ref('')
const inviteCandidates = ref<Member[]>([])
const invitingId = ref<number | null>(null)

function openInviteDialog(): void {
  if (!spacesStore.canInvite) {
    message.error('访客不能邀请成员')
    return
  }
  inviteOpen.value = true
}

async function doInviteSearch() {
  if (!spacesStore.canInvite || !inviteKeyword.value.trim()) return
  try {
    const data = await fetchMembersByPrefix(inviteKeyword.value.trim())
    const currentIds = new Set(spacesStore.activeMembers.map((m) => m.user_id))
    inviteCandidates.value = data.filter((m) => !currentIds.has(m.id))
  } catch {
    message.error('搜索失败，请稍后重试')
  }
}

async function inviteUser(member: Member) {
  if (!spacesStore.canInvite) {
    message.error('访客不能邀请成员')
    return
  }
  invitingId.value = member.id
  try {
    await spacesStore.invite(member.id)
    message.success('邀请已发送')
    inviteCandidates.value = inviteCandidates.value.filter((m) => m.id !== member.id)
  } catch {
    message.error('邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

async function acceptSpaceInvite(memberId: number) {
  try {
    await spacesStore.resolve(memberId, 'accept')
    message.success('已加入家庭空间')
  } catch {
    message.error('操作失败，请稍后重试')
  }
}

async function rejectSpaceInvite(memberId: number) {
  try {
    await spacesStore.resolve(memberId, 'reject')
  } catch {
    message.error('操作失败，请稍后重试')
  }
}

function openRelationDialog() {
  relationDialogOpen.value = true
}

function openWizard(): void {
  wizardOpen.value = true
}

function closeWizard(): void {
  wizardOpen.value = false
}

function onCreated(result: { name: string; pin: string | null }): void {
  wizardOpen.value = false
  issuedName.value = result.name
  // 幂等重放时不回看一次性 PIN（仅首次可见）
  issuedPin.value = result.pin ?? ''
}

function dismissPin(): void {
  issuedPin.value = ''
  issuedName.value = ''
}

function openProfile(id: number): void {
  members.openDrawer(id)
}

function genderLabel(value: GenderType): string {
  return value === 'f' ? '女' : value === 'm' ? '男' : '不详'
}

function formatDate(value: StructuredDate | null): string {
  if (!value) return '不详'
  const prefix =
    value.cal_type === 'lunar' ? '农历 ' : value.cal_type === 'solar' ? '公历 ' : ''
  return value.date ? `${prefix}${value.date}` : '不详'
}

/** 确档状态徽章（design.md §3.4）：本人直读 /me 的 profile_status；
 * 他人按身份状态机投影——「这是我」确认是 claimed ⇆ identity_confirmed 的
 * 唯一合法联动（architecture §身份状态机），故 claim_status 即为确档投影。 */
function identityBadge(member: Member): { text: string; cls: string } {
  const confirmed =
    member.id === auth.user?.id
      ? auth.user?.profile_status === 'identity_confirmed'
      : member.claim_status === 'claimed'
  return confirmed
    ? { text: '已确档', cls: 'fg-badge--confirmed' }
    : { text: '待确档', cls: 'fg-badge--provisional' }
}

function goSettings(): void {
  void router.push({ name: 'settings' })
}

// ---- 空间切换器：空间类型标识（design.md §3.3 Household=共同生活 / Lineage=谱系） ----
interface SpaceOption extends SelectOption {
  kind: 'household' | 'lineage'
}

const spaceOptions = computed<SpaceOption[]>(() =>
  spacesStore.spaces.map((s) => ({ label: s.name, value: s.id, kind: s.kind })),
)

/** Householder=共同生活（屋形）/ Lineage=谱系（双亲+子女节点树） */
const SPACE_KIND_ICONS: Record<SpaceOption['kind'], string> = {
  household:
    'M12 3.2 3 10.5h2.4V20h5.1v-5.4h3V20h5.1v-9.5H21L12 3.2Z',
  lineage:
    'M6 2.5a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm12 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM6 10.5c-1.66 0-3 1.34-3 3v2h2v-2a1 1 0 0 1 1-1h4v3h4v-3h4a1 1 0 0 1 1 1v2h2v-2c0-1.66-1.34-3-3-3h-4.5v-1.7h-3v1.7H6Zm6 6.5a2.75 2.75 0 1 0 0 5.5 2.75 2.75 0 0 0 0-5.5Z',
}

function renderSpaceLabel(option: SelectOption): VNodeChild {
  const kind = (option as SpaceOption).kind ?? 'household'
  return h('span', { class: 'fg-space-option' }, [
    h('span', { class: ['space-kind-icon', kind === 'lineage' ? 'is-lineage' : 'is-household'] }, [
      h('svg', { viewBox: '0 0 24 24', width: 14, height: 14, fill: 'currentColor', 'aria-hidden': 'true' }, [
        h('path', { d: SPACE_KIND_ICONS[kind] }),
      ]),
    ]),
    h('span', { class: 'space-kind-text' }, kind === 'lineage' ? '谱系' : '共同生活'),
    h('span', { class: 'space-option-name' }, String(option.label ?? '')),
  ])
}
</script>

<template>
  <main class="home-view">
    <!-- 过渡态：页面标题与动作区保留在内容区（导航收敛归壳，P5 收尾） -->
    <header class="topbar">
      <h1 class="title">你好，{{ auth.user?.name }}</h1>
      <div class="actions">
        <NButton type="primary" data-test="open-wizard" @click="openWizard">添加家人</NButton>
        <NBadge
          :value="graphStore.pendingIncomingCount"
          :show="graphStore.pendingIncomingCount > 0"
        >
          <NButton data-test="open-relation-dialog" @click="openRelationDialog">添加关系</NButton>
        </NBadge>
        <NButton data-test="go-family-space" @click="router.push({ name: 'family-space' })">
          家庭空间
        </NButton>
        <NButton data-test="go-settings" @click="goSettings">设置</NButton>
      </div>
    </header>

    <!-- 家庭空间区：创建、切换、成员邀请与管理员申请入口 -->
    <section class="space-section" data-test="space-section">
      <template v-if="spacesStore.spaces.length === 0">
        <NAlert type="info" :show-icon="true">
          你还没有家庭空间。可以先创建一个家庭空间，再邀请家人加入。
          <NButton size="small" type="primary" class="inline-action" data-test="create-space" @click="createSpaceOpen = true">
            创建家庭空间
          </NButton>
        </NAlert>
      </template>
      <template v-else>
        <div class="space-bar">
          <NSelect
            v-model:value="spacesStore.currentSpaceId"
            class="space-switcher"
            :options="spaceOptions"
            :render-label="renderSpaceLabel"
            data-test="space-switcher"
            aria-label="切换家庭空间"
            @update:value="(id: number) => spacesStore.loadMembers(id)"
          />
          <NButton
            v-if="spacesStore.canInvite"
            size="small"
            secondary
            data-test="invite-member"
            @click="openInviteDialog"
          >邀请成员</NButton>
          <!-- 入口只在服务端给出候选目标时出现：资格判定不在前端 -->
          <NButton
            v-if="eligibleTargets.length > 0"
            size="small"
            secondary
            data-test="apply-space-admin"
            @click="openApplyDialog"
          >申请接手族谱空间</NButton>

        </div>
        <div v-for="inv in spacesStore.pendingForMe" :key="inv.id" class="invite-row" data-test="space-invite">
          <span>「{{ spacesStore.spaces.find((s) => s.id === inv.space_id)?.name ?? '未知空间' }}」邀请你加入</span>
          <span class="invite-actions">
            <NButton size="tiny" type="primary" data-test="accept-invite" @click="acceptSpaceInvite(inv.id)">接受</NButton>
            <NButton size="tiny" secondary data-test="reject-invite" @click="rejectSpaceInvite(inv.id)">拒绝</NButton>
          </span>
        </div>
      </template>
      <!-- 原管理员交接工单：只有我本人是目标空间现任管理员时才会收到 -->
      <div
        v-for="consent in myConsents"
        :key="`consent-${consent.id}`"
        class="consent-row"
        data-test="transfer-consent-row"
      >
        <div class="consent-head">
          <span class="consent-text">{{ consentText(consent) }}</span>
          <span class="fg-badge fg-badge--proposed">待你决定</span>
        </div>
        <p class="consent-hint">
          同意后你将降为普通成员，该空间管理员由申请人接任；谢绝会终止这次申请。
        </p>
        <div class="consent-actions">
          <NInput
            v-model:value="consentReason[consent.id]"
            size="small"
            class="consent-reason"
            placeholder="谢绝需填写理由（同意可留空）"
            :data-test="`consent-reason-${consent.id}`"
          />
          <NButton
            size="small"
            type="primary"
            :loading="respondingConsentId === consent.id"
            :data-test="`consent-accept-${consent.id}`"
            @click="respondConsent(consent, 'accept')"
          >同意交接</NButton>
          <NButton
            size="small"
            secondary
            :loading="respondingConsentId === consent.id"
            :data-test="`consent-reject-${consent.id}`"
            @click="respondConsent(consent, 'reject')"
          >谢绝</NButton>
        </div>
      </div>

      <!-- 我的管理者申请状态（仅已有空间 space_admin 申请） -->
      <div
        v-for="app in myApplications"
        :key="app.id"
        class="invite-row"
        data-test="manager-application-row"
      >
        <span class="application-text">
          {{ applicationText(app) }}
          <span v-if="app.status === 'rejected' && app.decision_note" class="application-note">
            平台备注：{{ app.decision_note }}
          </span>
        </span>
        <span class="fg-badge" :class="applicationBadge(app).cls">
          {{ applicationBadge(app).text }}
        </span>
      </div>
    </section>

    <NSpin :show="members.loading">
      <section class="member-list" data-test="member-list">
        <NEmpty
          v-if="!members.loading && members.members.length === 0"
          class="member-empty"
          description="还没有任何家人档案"
        >
          <NButton type="primary" data-test="empty-add" @click="openWizard">添加第一位家人</NButton>
        </NEmpty>

        <template v-else>
          <article
            v-for="member in members.members"
            :key="member.id"
            class="member-card"
            data-test="member-card"
            @click="openProfile(member.id)"
          >
            <!-- 头像位：无头像用姓字纸牌（与画布名牌同一隐喻，P3 落地 MemberNode） -->
            <span class="avatar" aria-hidden="true">{{ member.name.slice(0, 1) }}</span>

            <div class="card-main">
              <div class="card-name-row">
                <span class="member-name">{{ member.name }}</span>
                <span v-if="member.id === auth.user?.id" class="fg-badge fg-badge--accent">我自己</span>
              </div>
              <div class="member-meta">
                <span>{{ genderLabel(member.gender) }}</span>
                <span class="meta-sep" aria-hidden="true">·</span>
                <span>生 {{ formatDate(member.birth) }}</span>
                <template v-if="member.death?.date">
                  <span class="meta-sep" aria-hidden="true">·</span>
                  <span>卒 {{ formatDate(member.death) }}</span>
                </template>
              </div>
            </div>

            <div class="card-side">
              <span class="fg-badge" :class="identityBadge(member).cls">
                {{ identityBadge(member).text }}
              </span>
              <span class="fg-badge fg-badge--neutral">
                {{ member.privacy_mode === 'handover' ? '移交本人' : '永久管理' }}
              </span>
              <NButton size="tiny" quaternary type="primary" class="card-open" @click.stop="openProfile(member.id)">
                查看档案
              </NButton>
            </div>
          </article>
        </template>
      </section>
    </NSpin>

    <MemberCreateWizard v-if="wizardOpen" @close="closeWizard" @created="onCreated" />
    <AddRelationDialog v-model:visible="relationDialogOpen" />

    <OneTimePinDialog
      v-if="issuedPin !== ''"
      :pin="issuedPin"
      :member-name="issuedName"
      @close="dismissPin"
    />

    <ProfileDrawer
      v-if="members.drawerTargetId !== null"
      :member-id="members.drawerTargetId"
      @close="members.closeDrawer()"
    />

    <NModal v-model:show="createSpaceOpen" preset="card" title="创建家庭空间" data-test="create-space-dialog">
      <NInput
        v-model:value="newSpaceName"
        placeholder="例如：我们家"
        :maxlength="64"
        :input-props="spaceNameInputProps"
        @keyup.enter="submitCreateSpace"
      />
      <template #footer>
        <div class="modal-actions">
          <NButton @click="createSpaceOpen = false">取消</NButton>
          <NButton type="primary" data-test="space-create-submit" @click="submitCreateSpace">创建</NButton>
        </div>
      </template>
    </NModal>

    <NModal
      v-model:show="applyOpen"
      preset="card"
      title="申请接手族谱空间管理员"
      data-test="apply-space-admin-dialog"
    >
      <p class="apply-hint">
        选择你要接手的族谱空间。提交后由系统管理员受理，并需该空间现任管理员本人同意才会换人。
      </p>
      <NEmpty
        v-if="eligibleTargets.length === 0"
        size="small"
        description="当前没有可申请的族谱空间"
        data-test="apply-targets-empty"
      />
      <ul v-else class="target-list">
        <li
          v-for="target in eligibleTargets"
          :key="target.space_id"
          class="target-card"
          :data-test="`apply-target-${target.space_id}`"
        >
          <div class="target-main">
            <span class="target-name">{{ target.space_name }}</span>
            <span class="fg-badge fg-badge--accent">族谱空间</span>
          </div>
          <p class="target-meta">
            现任管理员：{{ target.current_manager_name ?? '暂无' }}
          </p>
          <NButton
            size="small"
            type="primary"
            secondary
            :disabled="target.has_pending_application"
            :loading="applyingSpaceId === target.space_id"
            :data-test="`apply-target-submit-${target.space_id}`"
            @click="applyForTarget(target)"
          >
            {{ target.has_pending_application ? '已有在办申请' : '申请接手' }}
          </NButton>
        </li>
      </ul>
      <template #footer>
        <div class="modal-actions">
          <NButton @click="applyOpen = false">关闭</NButton>
        </div>
      </template>
    </NModal>

    <NModal v-model:show="inviteOpen" preset="card" title="邀请成员" data-test="invite-dialog">
      <NInput
        v-model:value="inviteKeyword"
        placeholder="输入名字前缀搜索"
        :input-props="inviteSearchInputProps"
        @keyup.enter="doInviteSearch"
      />
      <ul class="candidates">
        <li v-for="m in inviteCandidates" :key="m.id" class="candidate-row">
          <span>{{ m.name }}（#{{ m.id }}）</span>
          <NButton
            size="tiny"
            type="primary"
            secondary
            :loading="invitingId === m.id"
            :data-test="`invite-user-${m.id}`"
            @click="inviteUser(m)"
          >
            邀请
          </NButton>
        </li>
      </ul>
      <template #footer>
        <div class="modal-actions">
          <NButton @click="inviteOpen = false">关闭</NButton>
        </div>
      </template>
    </NModal>
  </main>
</template>

<style scoped>
.home-view {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 16px 40px;
  box-sizing: border-box;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

.title {
  margin: 0;
  font-family: var(--fg-font-display);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--fg-ink);
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.space-section {
  margin-bottom: 20px;
}

.space-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.space-switcher {
  width: 260px;
}

.invite-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent);
  border-radius: var(--fg-radius-control);
}

.invite-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}

.application-text {
  min-width: 0;
}

.application-note {
  display: block;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.apply-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.apply-name {
  margin-top: 12px;
}

.target-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.target-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background-color: var(--fg-surface-sunken);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
}

.target-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.target-name {
  font-size: 14px;
  color: var(--fg-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-meta {
  margin: 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.target-card :deep(.n-button) {
  align-self: flex-start;
}

.consent-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  padding: 12px;
  font-size: 13px;
  color: var(--fg-ink);
  background-color: color-mix(in srgb, var(--fg-status-proposed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-proposed) 30%, transparent);
  border-radius: var(--fg-radius-control);
}

.consent-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.consent-text {
  min-width: 0;
}

.consent-hint {
  margin: 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
  line-height: 1.5;
}

.consent-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.consent-reason {
  flex: 1;
  min-width: 0;
}

@media (max-width: 560px) {
  .consent-actions {
    flex-direction: column;
    align-items: stretch;
  }
}

.member-list {
  min-height: 120px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.member-empty {
  padding: 40px 0;
}

/* 成员卡：横向立牌——纸墨=纸面立牌（直角 + 发丝线 + 悬停微浮起）；
   清雅=白底大圆角柔和阴影。观感差异全部由 token 驱动 */
.member-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  background-color: var(--fg-surface-raised);
  border: 1px solid var(--fg-line-strong);
  border-radius: var(--fg-radius-card);
  box-shadow: var(--fg-shadow-card);
  cursor: pointer;
  transition:
    box-shadow 0.2s,
    transform 0.2s,
    border-color 0.2s;
}

.member-card:hover {
  box-shadow: var(--fg-shadow-raised);
  transform: translateY(-1px);
  border-color: var(--fg-accent);
}

/* 姓字纸牌头像位：主色柔底 + 宋体字（大字 ≥3:1） */
.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  font-family: var(--fg-font-display);
  font-size: 20px;
  font-weight: 700;
  color: var(--fg-accent);
  background-color: var(--fg-accent-soft);
  border-radius: var(--fg-radius-control);
}

[data-theme='modern'] .avatar {
  border-radius: 999px;
}

.card-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.card-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.member-name {
  font-family: var(--fg-font-display);
  font-size: 16px;
  font-weight: 700;
  color: var(--fg-ink);
}

.member-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.meta-sep {
  color: var(--fg-ink-faint);
}

.card-side {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.card-open {
  flex-shrink: 0;
}

.inline-action {
  margin-left: 8px;
  vertical-align: middle;
}

.candidates {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
}

.candidate-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  color: var(--fg-ink);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 640px) {
  .member-card {
    flex-wrap: wrap;
  }

  .card-side {
    width: 100%;
    justify-content: flex-start;
    padding-left: 58px;
  }
}
</style>

<style>
/* n-select 触发器与下拉菜单 teleport / 内部渲染，scopeId 不可达：
   空间类型标识以唯一 fg-space-* 类承载（图标色 ≥3:1：confirmed/accent 对卡面均达标） */
.fg-space-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.space-kind-icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.space-kind-icon.is-household {
  color: var(--fg-status-confirmed);
}

.space-kind-icon.is-lineage {
  color: var(--fg-accent);
}

.space-kind-text {
  font-size: 12px;
  color: var(--fg-ink-secondary);
  flex-shrink: 0;
}

.space-option-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* n-modal 卡片根节点 teleport 到 body：用 data-test 锚定宽度 */
[data-test='create-space-dialog'],
[data-test='apply-space-admin-dialog'] {
  width: min(460px, calc(100vw - 48px));
}


[data-test='invite-dialog'] {
  width: min(380px, calc(100vw - 48px));
}
</style>
