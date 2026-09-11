<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NRadioButton, NRadioGroup, NSpin } from 'naive-ui'
import { ArrowUpRight, Bell, Crown, LayoutGrid, List, Network, Pencil, ShieldCheck, UserRoundPlus, UsersRound } from 'lucide-vue-next'
import MaskedField from '@/components/common/MaskedField.vue'
import InviteMemberDialog from '@/components/member/InviteMemberDialog.vue'
import SpaceCreateDialog from '@/components/member/SpaceCreateDialog.vue'
import { describeLoadError } from '@/api/loadError'
import { useSpaceContext } from '@/composables/useSpaceContext'
import { useAuthStore } from '@/stores/auth'
import { useHouseholdCardStore } from '@/stores/household'
import { useSpacesStore } from '@/stores/spaces'
import type { HouseholdCardMember } from '@/types/api'

// 成员仅来自服务端 household 投影；本人资料来自 auth。失败时不拼接其他数据源。
const router = useRouter()
const auth = useAuthStore()
const spaces = useSpacesStore()
const household = useHouseholdCardStore()
const spaceContext = useSpaceContext()
const viewMode = ref<'grid' | 'list'>('grid')
const inviteOpen = ref(false)
const createOpen = ref(false)
const noLineageHint = ref(false)
const spaceId = computed(() => spaces.currentSpaceId)
const isHouseholdContext = computed(() => spaces.currentSpace?.kind === 'household')
const card = computed(() => (spaceId.value === null ? null : household.forSpace(spaceId.value)))
const loading = computed(() => spaceId.value !== null && household.isLoading(spaceId.value))
const loadError = computed(() => (spaceId.value === null ? null : household.errorFor(spaceId.value)))
/** 按真实失败原因分类的可行动文案（404 未部署 / 403 无权 / 503 维护 / 网络偏斜） */
const errorCopy = computed(() =>
  loadError.value === null ? null : describeLoadError(loadError.value, '家庭卡'),
)
const householdName = computed(() => card.value?.space_name ?? spaces.currentSpace?.name ?? '我的家庭')
const members = computed<HouseholdCardMember[]>(() => card.value?.members ?? [])
const memberCount = computed(() => members.value.length)
const householdSpaces = computed(() => spaces.spaces.filter((space) => space.kind === 'household'))
const selfStatus = computed(() => auth.user?.profile_status === 'identity_confirmed' ? '已确档' : '待确档')

async function loadCard(): Promise<void> {
  if (spaceId.value === null || !isHouseholdContext.value) return
  await household.load(spaceId.value).catch(() => undefined)
}
function retry(): void {
  if (spaceId.value !== null) void household.refresh(spaceId.value).catch(() => undefined)
}
onMounted(() => { void loadCard() })
watch(spaceId, () => {
  noLineageHint.value = false
  void loadCard()
})
async function exitToFamilyTree(): Promise<void> {
  const current = spaces.currentSpace
  const target = current ? spaces.lineageForSpace(current.id) : null
  if (!target) {
    noLineageHint.value = true
    return
  }
  // 只切到本家庭所属的 lineage（显式配对优先，owner 唯一匹配回退），不跳到其他家族。
  await spaceContext.switchSpace(target.id)
}
function goToSettings(): void { void router.push({ name: 'settings' }) }
function goToNotifications(): void { void router.push({ name: 'notifications' }) }
function openMember(member: HouseholdCardMember): void {
  if (member.user_id === auth.user?.id) return
  void router.push({
    name: 'person-profile',
    params: { userId: String(member.user_id) },
    state: { fgBackTo: 'home' },
  })
}
function genderText(value: HouseholdCardMember['display']['gender']): string | null {
  if (typeof value !== 'string') return null
  return value === 'f' ? '女' : value === 'm' ? '男' : '不详'
}
</script>

<template>
  <main class="household-card-view" data-test="household-card-view">
    <div class="exit-row">
      <span class="page-label"><UsersRound :size="17" aria-hidden="true" /> 家庭空间</span>
      <button type="button" class="exit-button" data-test="exit-to-family-tree"
        aria-label="退出到家族树" title="退出到家族树" @click="exitToFamilyTree">
        <span>家族树</span><ArrowUpRight :size="18" aria-hidden="true" />
      </button>
    </div>
    <NAlert v-if="noLineageHint" type="info" :show-icon="true" class="context-alert" data-test="no-lineage-hint">
      当前没有可用的家族空间，已保留在家庭卡。可以先在家庭空间邀请家人，或创建一个家族空间后再进入家族树。
    </NAlert>
    <section v-if="!isHouseholdContext" class="context-panel" data-test="household-context-panel">
      <h1 class="card-title">我的家庭</h1>
      <NAlert v-if="householdSpaces.length > 0" type="info" :show-icon="true">正在打开当前家庭空间…</NAlert>
      <NAlert v-else type="info" :show-icon="true" data-test="no-household-hint">
        你还没有家庭空间。可以创建一个家庭空间，再邀请家人加入。
        <NButton size="small" type="primary" class="inline-action" @click="createOpen = true">创建家庭空间</NButton>
      </NAlert>
    </section>
    <article v-else class="family-space-hero" data-test="family-space-hero">
      <header class="family-space-head">
        <div class="family-space-identity">
          <p class="space-kind">我的家庭</p>
          <h1 class="card-title" data-test="household-name">{{ householdName }}</h1>
        </div>
        <span v-if="spaces.isSpaceAdmin" class="space-admin" data-test="household-admin-badge">
          <Crown :size="14" aria-hidden="true" /> 空间管理员
        </span>
      </header>
      <section v-if="loadError !== null" class="status-panel" data-test="household-load-error">
        <h2 class="status-title">{{ errorCopy?.title }}</h2>
        <p class="status-text">{{ errorCopy?.text }} 已按安全策略不展示任何成员数据。</p>
        <NButton size="small" data-test="contract-retry" @click="retry">重新加载</NButton>
      </section>
      <NSpin v-else-if="loading && card === null" class="loading-spin" :show="true" aria-label="正在加载家庭空间" />
      <template v-else-if="card !== null">
        <div class="card-columns">
          <section class="self-card" data-test="self-profile">
            <div class="self-head">
              <span class="avatar avatar--self" aria-hidden="true">{{ (auth.user?.name ?? '我').slice(0, 1) }}</span>
              <div class="self-main">
                <span class="self-name" data-test="self-name">{{ auth.user?.name ?? '我' }}</span>
                <span class="self-status" data-test="self-status-badge">
                  <ShieldCheck :size="13" aria-hidden="true" />{{ selfStatus }}
                </span>
              </div>
            </div>
            <div class="self-actions">
              <NButton size="small" quaternary data-test="go-edit-profile" @click="goToSettings">
                <template #icon><Pencil :size="16" /></template>编辑资料
              </NButton>
              <NButton size="small" quaternary data-test="go-disclosure-settings" @click="goToSettings">
                <template #icon><ShieldCheck :size="16" /></template>隐私与公示设置
              </NButton>
            </div>
          </section>
          <section class="members-card" data-test="household-members">
            <div class="members-head">
              <h2 class="section-title">家庭成员 <span>{{ memberCount }}</span></h2>
              <NRadioGroup v-model:value="viewMode" size="small" name="member-view-mode"
                data-test="member-view-toggle" aria-label="成员展示方式">
                <NRadioButton value="grid" aria-label="网格" title="网格"><LayoutGrid :size="16" aria-hidden="true" /></NRadioButton>
                <NRadioButton value="list" aria-label="列表" title="列表"><List :size="16" aria-hidden="true" /></NRadioButton>
              </NRadioGroup>
            </div>
            <div v-if="memberCount === 0" class="members-empty" data-test="household-empty">
              <UsersRound :size="32" :stroke-width="1.2" aria-hidden="true" />
              <p class="empty-hint" data-test="empty-hint">{{ card.allowed_actions.empty_state_hint ?? '这个家庭还没有确认的成员。' }}</p>
              <div class="empty-actions">
                <NButton v-if="card.allowed_actions.can_invite_members" type="primary" size="small" data-test="invite-entry" @click="inviteOpen = true">
                  <template #icon><UserRoundPlus :size="16" /></template>邀请家人
                </NButton>
                <NButton v-if="card.allowed_actions.can_create_household" size="small" secondary data-test="create-household-entry" @click="createOpen = true">创建家庭空间</NButton>
              </div>
            </div>
            <div v-else-if="viewMode === 'grid'" class="member-grid" data-test="member-grid">
              <button v-for="member in members" :key="member.user_id" type="button" class="member-card"
                :class="{ 'is-self': member.user_id === auth.user?.id }"
                :data-test="'member-card-' + member.user_id" @click="openMember(member)">
                <span class="member-identity">
                  <span class="avatar avatar--small" aria-hidden="true">{{ member.display.name.slice(0, 1) }}</span>
                  <span class="member-heading"><span class="member-name">{{ member.display.name }}</span><span class="member-label" data-test="household-label">{{ member.household_label }}</span></span>
                  <span v-if="member.user_id === auth.user?.id" class="self-mark">我</span>
                </span>
                <span class="member-meta">
                  <template v-if="genderText(member.display.gender)">{{ genderText(member.display.gender) }}</template>
                  <MaskedField v-else :value="member.display.gender" />
                  <template v-if="member.display.birth !== null && !('__masked__' in member.display.birth)">· 生 {{ member.display.birth.date ?? '不详' }}</template>
                  <template v-else-if="member.display.birth !== null">· 生 <MaskedField :value="member.display.birth" /></template>
                </span>
                <span class="member-visibility" data-test="member-visibility">{{ member.visibility_level === 'lineage_summary' ? '族谱摘要' : member.visibility_level === 'self_private' ? '仅本人' : '家庭可见' }}</span>
              </button>
            </div>
            <ul v-else class="member-list" data-test="member-list">
              <li v-for="member in members" :key="member.user_id" class="member-row"
                :data-test="'member-row-' + member.user_id" role="button" tabindex="0"
                @click="openMember(member)" @keyup.enter="openMember(member)" @keydown.space.prevent="openMember(member)">
                <span class="avatar avatar--small" aria-hidden="true">{{ member.display.name.slice(0, 1) }}</span>
                <span class="member-heading"><span class="member-name">{{ member.display.name }}</span><span class="member-label" data-test="household-label">{{ member.household_label }}</span></span>
                <span v-if="member.user_id === auth.user?.id" class="self-mark">我</span>
                <span class="member-meta">
                  <template v-if="genderText(member.display.gender)">{{ genderText(member.display.gender) }}</template>
                  <MaskedField v-else :value="member.display.gender" />
                  <template v-if="member.display.birth !== null && !('__masked__' in member.display.birth)">· 生 {{ member.display.birth.date ?? '不详' }}</template>
                  <template v-else-if="member.display.birth !== null">· 生 <MaskedField :value="member.display.birth" /></template>
                </span>
              </li>
            </ul>
          </section>
        </div>
        <footer class="family-space-footer">
          <div class="family-status" data-test="family-status">
            <span class="status-item"><UsersRound :size="15" aria-hidden="true" /><strong data-test="member-count">{{ memberCount }}</strong> 位家人</span>
            <span class="status-item status-item--version">数据版本 <strong data-test="household-version">v{{ card.view_version }}</strong></span>
          </div>
          <nav class="fab-dock" data-test="household-fab-dock" aria-label="家庭卡操作">
            <button type="button" class="fg-fab-btn" aria-label="编辑资料" title="编辑资料" @click="goToSettings"><Pencil :size="18" aria-hidden="true" /></button>
            <button type="button" class="fg-fab-btn" data-test="go-notifications" aria-label="待办与通知" title="待办与通知" @click="goToNotifications"><Bell :size="18" aria-hidden="true" /></button>
            <button type="button" class="fg-fab-btn fg-fab-btn--accent" data-test="go-family-tree" aria-label="进入家族树" title="进入家族树" @click="exitToFamilyTree"><Network :size="18" aria-hidden="true" /></button>
          </nav>
        </footer>
      </template>
    </article>
    <InviteMemberDialog :visible="inviteOpen" @update:visible="inviteOpen = $event" />
    <SpaceCreateDialog :visible="createOpen" default-kind="household" @update:visible="createOpen = $event" />
  </main>
</template>

<style scoped>
.household-card-view { position: relative; display: flex; flex-direction: column; gap: 20px; width: 100%; max-width: 1320px; margin: 0 auto; padding: 32px 44px 100px; box-sizing: border-box; }
.exit-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-label { display: flex; gap: 9px; align-items: center; font-size: 13px; color: var(--fg-ink-secondary); }
.exit-button { min-height: 44px; display: inline-flex; align-items: center; gap: 8px; padding: 0 6px; color: var(--fg-ink-secondary); font: inherit; font-size: 12px; border: 0; background: none; cursor: pointer; }
.exit-button:hover { color: var(--fg-accent); }
.family-space-hero {
  position: relative; display: flex; flex-direction: column; min-height: 580px; padding: 36px 40px 24px; box-sizing: border-box;
  background: linear-gradient(125deg, color-mix(in srgb, var(--fg-ink) 9%, transparent), transparent 54%), var(--fg-glass-surface);
  border: 1px solid var(--fg-glass-border); border-top-color: color-mix(in srgb, var(--fg-ink) 30%, transparent); border-radius: 8px;
  backdrop-filter: blur(28px) saturate(115%); -webkit-backdrop-filter: blur(28px) saturate(115%);
  box-shadow: var(--fg-shadow-raised), 0 2px 0 color-mix(in srgb, var(--fg-surface) 60%, transparent), inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 10%, transparent);
  transition: transform 350ms ease, box-shadow 350ms ease, border-color 350ms ease;
}
.family-space-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-bottom: 32px; border-bottom: 1px solid var(--fg-glass-border); }
.family-space-identity { min-width: 0; }
.space-kind { margin: 0 0 10px; font-size: 12px; color: var(--fg-ink-secondary); }
.card-title { margin: 0; color: var(--fg-ink); font-family: var(--fg-font-display); font-size: 32px; line-height: 1.4; font-weight: 600; letter-spacing: 0; overflow-wrap: anywhere; }
.space-admin { display: flex; align-items: center; gap: 7px; flex-shrink: 0; font-size: 12px; color: var(--fg-accent); }
.card-columns { display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: 32px; flex: 1; padding: 32px 0; }
.self-card { display: flex; flex-direction: column; align-items: flex-start; gap: 28px; padding: 4px 28px 0 0; border-right: 1px solid var(--fg-glass-border); min-width: 0; }
.self-head, .self-main { display: flex; flex-direction: column; align-items: flex-start; gap: 18px; min-width: 0; }
.self-main { gap: 8px; }
.self-name { font-family: var(--fg-font-display); font-size: 23px; font-weight: 600; overflow-wrap: anywhere; }
.self-status { display: flex; gap: 5px; align-items: center; font-size: 11px; color: var(--fg-status-confirmed); }
.avatar { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; width: 72px; height: 72px; font-family: var(--fg-font-display); font-size: 30px; color: var(--fg-accent); border: 1px solid color-mix(in srgb, var(--fg-accent) 30%, transparent); border-radius: 50%; background: var(--fg-accent-soft); box-shadow: inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 12%, transparent); }
.avatar--small { width: 42px; height: 42px; font-size: 19px; color: var(--fg-ink-secondary); background: color-mix(in srgb, var(--fg-ink) 5%, transparent); border-color: var(--fg-glass-border); }
.self-actions { display: flex; flex-direction: column; gap: 5px; margin-left: -8px; }
.self-actions :deep(.n-button) { justify-content: flex-start; min-height: 40px; }
.members-card { min-width: 0; display: flex; flex-direction: column; gap: 20px; }
.members-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.section-title { margin: 0; font-size: 14px; font-weight: 550; }
.section-title span { margin-left: 8px; color: var(--fg-ink-faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.members-head :deep(.n-radio-button) { min-width: 40px; display: inline-flex; align-items: center; justify-content: center; }
.member-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); align-content: start; gap: 4px 20px; max-height: 420px; overflow-y: auto; padding: 2px; scrollbar-width: thin; scrollbar-color: var(--fg-line-strong) transparent; }
.member-card { display: flex; flex-direction: column; align-items: stretch; gap: 10px; min-height: 145px; min-width: 0; padding: 16px 8px; border: 0; border-bottom: 1px solid var(--fg-glass-border); background: transparent; color: var(--fg-ink); font: inherit; text-align: left; cursor: pointer; transition: background-color 180ms ease; }
.member-card:hover, .member-row:hover { background: color-mix(in srgb, var(--fg-ink) 4%, transparent); }
.member-identity { display: flex; align-items: center; gap: 10px; min-width: 0; }
.member-heading { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.member-name { font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.member-label { font-size: 11px; color: var(--fg-ink-secondary); overflow-wrap: anywhere; }
.self-mark { margin-left: auto; font-size: 11px; color: var(--fg-accent); flex-shrink: 0; }
.member-meta { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px; color: var(--fg-ink-secondary); font-size: 11px; }
.member-visibility { color: var(--fg-ink-faint); font-size: 10px; }
.member-list { display: flex; flex-direction: column; list-style: none; margin: 0; padding: 2px; max-height: 420px; overflow-y: auto; scrollbar-width: thin; }
.member-row { display: flex; align-items: center; gap: 12px; min-height: 76px; padding: 10px 8px; border-bottom: 1px solid var(--fg-glass-border); box-sizing: border-box; cursor: pointer; }
.member-row .member-meta { margin-left: auto; text-align: right; }
.members-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 18px; flex: 1; min-height: 220px; padding: 20px 0; color: var(--fg-ink-faint); }
.empty-hint { margin: 0; font-size: 13px; text-align: center; color: var(--fg-ink-secondary); }
.empty-actions, .context-switch { display: flex; flex-wrap: wrap; gap: 10px; }
.family-space-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-top: 20px; border-top: 1px solid var(--fg-glass-border); }
.family-status { display: flex; align-items: center; flex-wrap: wrap; gap: 20px; font-size: 11px; color: var(--fg-ink-secondary); }
.status-item { display: inline-flex; align-items: center; gap: 6px; }
.status-item strong { color: var(--fg-ink); font-weight: 500; font-variant-numeric: tabular-nums; }
.fab-dock { display: flex; gap: 8px; flex-shrink: 0; }
.fab-dock .fg-fab-btn { box-shadow: none; background: color-mix(in srgb, var(--fg-ink) 4%, transparent); }
.fab-dock .fg-fab-btn--accent { background: var(--fg-accent); }
.context-panel { display: flex; flex-direction: column; align-items: flex-start; gap: 20px; padding: 32px 0; }
.context-alert { max-width: 100%; }
.inline-action { margin: 8px; }
.status-panel { padding: 40px 0; }
.status-title { margin: 0 0 12px; font-size: 16px; }
.status-text { max-width: 560px; margin: 0 0 20px; font-size: 13px; line-height: 1.8; color: var(--fg-ink-secondary); }
.loading-spin { display: flex; justify-content: center; align-items: center; min-height: 320px; }
@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .family-space-hero:hover { transform: translateY(-4px); border-top-color: color-mix(in srgb, var(--fg-ink) 45%, transparent); box-shadow: var(--fg-shadow-raised), 0 8px 0 -5px var(--fg-glass-border), inset 0 1px 0 color-mix(in srgb, var(--fg-ink) 15%, transparent); }
}
@supports not (backdrop-filter: blur(12px)) { .family-space-hero { background: var(--fg-surface-raised); } }
@media (max-width: 1100px) {
  .household-card-view { padding: 24px 28px 88px; }
  .family-space-hero { padding: 28px 28px 24px; }
  .card-columns { grid-template-columns: 176px minmax(0, 1fr); gap: 24px; }
  .self-card { padding-right: 20px; }
}
@media (max-width: 768px) {
  .card-columns { grid-template-columns: 1fr; gap: 24px; padding: 24px 0; }
  .household-card-view { gap: 12px; padding: 16px 16px 88px; }
  .family-space-hero { min-height: 580px; padding: 24px 20px 20px; }
  .family-space-head { align-items: flex-start; flex-direction: column; gap: 12px; padding-bottom: 24px; }
  .card-title { font-size: 26px; }
  .space-kind { margin-bottom: 6px; }
  .self-card { padding: 0 0 24px; border-right: 0; border-bottom: 1px solid var(--fg-glass-border); gap: 16px; }
  .self-head { flex-direction: row; align-items: center; }
  .self-name { font-size: 20px; }
  .avatar--self { width: 56px; height: 56px; font-size: 25px; }
  .self-actions { flex-direction: row; flex-wrap: wrap; gap: 4px; }
  .member-grid { grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 4px 12px; }
  .member-card { min-height: 134px; }
  .member-row { flex-wrap: wrap; }
  .member-row .member-meta { flex-basis: 100%; margin-left: 54px; text-align: left; }
  .family-space-footer { flex-wrap: wrap; gap: 18px; }
  .family-status { gap: 12px; }
  .fab-dock { margin-left: auto; }
  .household-card-view :deep(.n-button--small-type),
  .household-card-view :deep(.n-radio-button) { min-height: 44px; }
}
</style>
