<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NEmpty, NSpin } from 'naive-ui'

import PendingProfileRefs from '@/components/member/PendingProfileRefs.vue'
import SpaceGovernancePanel from '@/components/member/SpaceGovernancePanel.vue'
import { useSpacesStore } from '@/stores/spaces'

/**
 * 独立家庭空间管理看板。
 * 进入路由前由 router 守卫确认目标 spaceId 是当前用户的 active owner/
 * space_admin membership；视图仍按目标空间加载，避免依赖用户可篡改的 UI 状态。
 */
const route = useRoute()
const router = useRouter()
const spaces = useSpacesStore()
const loading = computed(() => spaces.loading)
const targetSpaceId = computed(() => Number(route.params.spaceId))
const space = computed(() => spaces.spaces.find((item) => item.id === targetSpaceId.value) ?? null)
const hasAccess = computed(() => spaces.currentSpaceId === targetSpaceId.value && spaces.canManageSpace)
const roleLabel = computed(() =>
  spaces.currentRole === 'owner' ? '空间所有者' : spaces.currentRole === 'space_admin' ? '空间管理员' : '无空间管理权限',
)
const pendingCount = computed(() => spaces.members.filter((member) => member.status === 'pending').length)

onMounted(async () => {
  await spaces.loadMembers(targetSpaceId.value).catch(() => undefined)
})

function goFamilySpace(): void {
  void router.push({ name: 'family-space' })
}
</script>

<template>
  <main class="management-view" data-test="space-management-view">
    <header class="management-header">
      <div>
        <p class="eyebrow">空间治理</p>
        <h1>家庭空间管理</h1>
        <p class="description">仅空间所有者和空间管理员可以管理当前家庭空间的成员与移交。</p>
      </div>
      <NButton data-test="management-back" @click="goFamilySpace">返回家庭空间</NButton>
    </header>

    <NSpin :show="loading">
      <NAlert v-if="!hasAccess || !space" type="warning" :show-icon="true" data-test="management-denied">
        当前账号没有管理这个家庭空间的权限。
        <NButton size="small" secondary class="inline-action" @click="goFamilySpace">回到家庭空间</NButton>
      </NAlert>
      <template v-else>
        <section class="space-overview" data-test="space-overview">
          <div>
            <span class="label">空间名称</span>
            <strong data-test="management-space-name">{{ space.name }}</strong>
          </div>
          <div>
            <span class="label">空间类型</span>
            <span class="fg-badge" :class="space.kind === 'lineage' ? 'fg-badge--accent' : 'fg-badge--confirmed'" data-test="management-space-kind">
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
        </section>

        <NCard title="成员与空间治理" class="management-card">
          <SpaceGovernancePanel />
        </NCard>

        <NCard title="待确档引用" class="management-card">
          <PendingProfileRefs :refs="spaces.profileRefs" />
          <NEmpty v-if="spaces.profileRefs.length === 0" description="当前空间没有待确档引用" size="small" />
        </NCard>
      </template>
    </NSpin>
  </main>
</template>

<style scoped>
.management-view { max-width: 1120px; margin: 0 auto; padding: 32px 20px 48px; box-sizing: border-box; }
.management-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 24px; }
.eyebrow { margin: 0 0 5px; color: var(--fg-accent); font-size: 12px; letter-spacing: .12em; }
h1 { margin: 0 0 8px; color: var(--fg-ink); font-family: var(--fg-font-display); font-size: 30px; }
.description { margin: 0; color: var(--fg-ink-secondary); font-size: 14px; }
.space-overview { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.space-overview > div { display: flex; flex-direction: column; gap: 6px; min-width: 0; padding: 14px; background: var(--fg-surface-raised); border: 1px solid var(--fg-line); border-radius: var(--fg-radius-card); }
.label { color: var(--fg-ink-secondary); font-size: 12px; }
.space-overview strong { color: var(--fg-ink); font-size: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.management-card { margin-top: 16px; }
.inline-action { margin-left: 8px; }
@media (max-width: 768px) { .management-header { flex-direction: column; } .space-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 420px) { .management-view { padding: 22px 12px 36px; } .space-overview { grid-template-columns: 1fr 1fr; } h1 { font-size: 24px; } }
</style>
