<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import OneTimePinDialog from '@/components/member/OneTimePinDialog.vue'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { useMembersStore } from '@/stores/members'
import type { GenderType, StructuredDate } from '@/types/api'

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
})

function openWizard(): void {
  wizardOpen.value = true
}

function closeWizard(): void {
  wizardOpen.value = false
}

function onCreated(result: { name: string; pin: string }): void {
  wizardOpen.value = false
  issuedName.value = result.name
  issuedPin.value = result.pin // 弹窗关闭时置空，此后不可回看
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
        <el-button data-test="go-settings" @click="goSettings">设置</el-button>
      </div>
    </header>

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
