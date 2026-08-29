<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import OneTimePinDialog from '@/components/member/OneTimePinDialog.vue'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import AddRelationDialog from '@/components/member/AddRelationDialog.vue'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { fetchMembersByPrefix } from '@/api/members'
import { useMembersStore } from '@/stores/members'
import { useGraphStore } from '@/stores/graph'
import { useSpacesStore } from '@/stores/spaces'
import type { GenderType, Member, StructuredDate } from '@/types/api'

/**
 * M1 首页：「与我相关的档案」列表（自己 + 我创建的；admin 可见全部）。
 * m1d 后该列表被家庭空间画布取代（m1a design）。
 */
const auth = useAuthStore()
const members = useMembersStore()
const router = useRouter()

const wizardOpen = ref(false)
// 一次性凭据：仅存在于本组件内存，弹窗关闭即清空（不可回看）
const issuedPin = ref('')
const issuedName = ref('')

onMounted(() => {
  members.load().catch(() => ElMessage.error('档案列表加载失败，请稍后重试'))
  // 空间与待处理邀请（AD-3）；收到的连接请求红点（审批 UI 归 m2c）
  spacesStore.load().catch(() => undefined)
  graphStore.loadIncoming().catch(() => undefined)
})

const relationDialogOpen = ref(false)
const graphStore = useGraphStore()
const spacesStore = useSpacesStore()
const createSpaceOpen = ref(false)
const newSpaceName = ref('')

async function submitCreateSpace() {
  if (!newSpaceName.value.trim()) return
  try {
    await spacesStore.create(newSpaceName.value.trim())
    newSpaceName.value = ''
    createSpaceOpen.value = false
  } catch {
    ElMessage.error('创建空间失败，请稍后重试')
  }
}

const inviteOpen = ref(false)
const inviteKeyword = ref('')
const inviteCandidates = ref<Member[]>([])
const invitingId = ref<number | null>(null)

function openInviteDialog() {
  inviteOpen.value = true
}

async function doInviteSearch() {
  if (!inviteKeyword.value.trim()) return
  try {
    const data = await fetchMembersByPrefix(inviteKeyword.value.trim())
    const currentIds = new Set(spacesStore.activeMembers.map((m) => m.user_id))
    inviteCandidates.value = data.filter((m) => !currentIds.has(m.id))
  } catch {
    ElMessage.error('搜索失败，请稍后重试')
  }
}

async function inviteUser(member: Member) {
  invitingId.value = member.id
  try {
    await spacesStore.invite(member.id)
    ElMessage.success('邀请已发送')
    inviteCandidates.value = inviteCandidates.value.filter((m) => m.id !== member.id)
  } catch {
    ElMessage.error('邀请失败，请稍后重试')
  } finally {
    invitingId.value = null
  }
}

async function acceptSpaceInvite(memberId: number) {
  try {
    await spacesStore.resolve(memberId, 'accept')
    ElMessage.success('已加入家庭空间')
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  }
}

async function rejectSpaceInvite(memberId: number) {
  try {
    await spacesStore.resolve(memberId, 'reject')
  } catch {
    ElMessage.error('操作失败，请稍后重试')
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

function goSettings(): void {
  void router.push({ name: 'settings' })
}
</script>

<template>
  <main class="home-view">
    <header class="topbar">
      <h1 class="title">你好，{{ auth.user?.name }}</h1>
      <div class="actions">
        <el-button type="primary" data-test="open-wizard" @click="openWizard">添加家人</el-button>
        <el-button data-test="open-relation-dialog" @click="openRelationDialog">
          添加关系<el-badge
            v-if="graphStore.pendingIncomingCount > 0"
            :value="graphStore.pendingIncomingCount"
            class="relation-badge"
          />
        </el-button>
        <el-button data-test="go-settings" @click="goSettings">设置</el-button>
      </div>
    </header>

    <!-- 家庭空间区（m1c）：空态引导建默认空间 / 空间切换+成员+邀请 -->
    <section class="space-section" data-test="space-section">
      <template v-if="spacesStore.spaces.length === 0">
        <el-alert type="info" :closable="false">
          你还没有家庭空间。创建一个，把家人邀请进来吧。
          <el-button size="small" type="primary" data-test="create-space" @click="createSpaceOpen = true">
            创建家庭空间
          </el-button>
        </el-alert>
      </template>
      <template v-else>
        <div class="space-bar">
          <el-select
            v-model="spacesStore.currentSpaceId"
            data-test="space-switcher"
            style="width: 200px"
            @change="(id: number) => spacesStore.loadMembers(id)"
          >
            <el-option v-for="s in spacesStore.spaces" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-button size="small" data-test="invite-member" @click="openInviteDialog">邀请成员</el-button>
        </div>
        <div v-for="inv in spacesStore.pendingForMe" :key="inv.id" class="invite-row" data-test="space-invite">
          <span>「{{ spacesStore.spaces.find((s) => s.id === inv.space_id)?.name ?? '未知空间' }}」邀请你加入</span>
          <el-button size="small" type="primary" data-test="accept-invite" @click="acceptSpaceInvite(inv.id)">接受</el-button>
          <el-button size="small" data-test="reject-invite" @click="rejectSpaceInvite(inv.id)">拒绝</el-button>
        </div>
      </template>
    </section>

    <section v-loading="members.loading" class="member-list" data-test="member-list">
      <el-empty v-if="!members.loading && members.members.length === 0" description="还没有任何家人档案">
        <el-button type="primary" data-test="empty-add" @click="openWizard">添加第一位家人</el-button>
      </el-empty>

      <template v-else>
        <el-card
          v-for="member in members.members"
          :key="member.id"
          shadow="hover"
          class="member-card"
          data-test="member-card"
          @click="openProfile(member.id)"
        >
          <div class="card-body">
            <div class="card-main">
              <span class="member-name">{{ member.name }}</span>
              <span class="member-meta">{{ genderLabel(member.gender) }}</span>
              <span class="member-meta">{{ formatDate(member.birth) }}</span>
            </div>
            <div class="card-tags">
              <el-tag size="small" type="info">
                {{ member.privacy_mode === 'handover' ? '移交本人' : '永久管理' }}
              </el-tag>
              <el-tag
                v-if="member.id !== auth.user?.id"
                size="small"
                :type="member.claim_status === 'claimed' ? 'success' : 'warning'"
              >
                {{ member.claim_status === 'claimed' ? '已认领' : '待认领' }}
              </el-tag>
              <el-tag v-if="member.id === auth.user?.id" size="small" type="success">我自己</el-tag>
            </div>
          </div>
        </el-card>
      </template>
    </section>

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
      <el-dialog v-model="createSpaceOpen" title="创建家庭空间" width="360px" data-test="create-space-dialog">
      <el-input
        v-model="newSpaceName"
        placeholder="例如：我们家"
        maxlength="64"
        data-test="space-name-input"
        @keyup.enter="submitCreateSpace"
      />
      <template #footer>
        <el-button @click="createSpaceOpen = false">取消</el-button>
        <el-button type="primary" data-test="space-create-submit" @click="submitCreateSpace">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="inviteOpen" title="邀请成员" width="380px" data-test="invite-dialog">
      <el-input
        v-model="inviteKeyword"
        placeholder="输入名字前缀搜索"
        data-test="invite-search"
        @keyup.enter="doInviteSearch"
      />
      <ul class="candidates">
        <li v-for="m in inviteCandidates" :key="m.id" class="candidate-row">
          <span>{{ m.name }}（#{{ m.id }}）</span>
          <el-button
            size="small"
            type="primary"
            :loading="invitingId === m.id"
            :data-test="`invite-user-${m.id}`"
            @click="inviteUser(m)"
          >
            邀请
          </el-button>
        </li>
      </ul>
      <template #footer>
        <el-button @click="inviteOpen = false">关闭</el-button>
      </template>
    </el-dialog>
</main>
</template>

<style scoped>
.home-view {
  max-width: 720px;
  margin: 0 auto;
  padding: 32px 16px;
  min-height: 100vh;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.title {
  margin: 0;
  font-size: 22px;
}

.actions {
  display: flex;
  gap: 8px;
}

.member-list {
  min-height: 120px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.member-card {
  cursor: pointer;
}

.card-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-main {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.member-name {
  font-size: 16px;
  font-weight: 600;
}

.member-meta {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.card-tags {
  display: flex;
  gap: 6px;
}
</style>
