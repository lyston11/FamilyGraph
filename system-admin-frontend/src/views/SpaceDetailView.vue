<script setup lang="ts">
/**
 * 空间详情：概要 + 成员/档案、关系、已确认事实、通知四个钻取面板。
 *
 * 敏感流程（PRD FE-F5）：
 * - 成员档案（user 票据）、关系与 confirmed 事实（space 票据）必须先填理由
 *   → POST /v1/access-sessions → 票据只存内存 → 请求带 X-Admin-Access-Session；
 * - 票据失效（403）→ 清内存票据 → 重新弹理由表单；
 * - 未知空间 → 统一安全 404 空态，不暴露存在性。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiSpaceDetail, apiSpaceMembers } from '@/api/read'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import ReasonModal from '@/components/ReasonModal.vue'
import MemberProfilePanel from '@/components/MemberProfilePanel.vue'
import SpaceRelationsPanel from '@/components/SpaceRelationsPanel.vue'
import SpaceFactsPanel from '@/components/SpaceFactsPanel.vue'
import SpaceNotificationsPanel from '@/components/SpaceNotificationsPanel.vue'
import { useAccessTicket } from '@/composables/useAccessTicket'
import type {
  AdminPageOut,
  AdminSpaceDetailOut,
  AdminMemberOut,
  AccessTargetType,
} from '@/types/api'

const route = useRoute()
const spaceId = computed(() => Number(route.params['spaceId']))

const space = ref<AdminSpaceDetailOut | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const notFound = ref(false)

// ---- 成员列表（普通读取）----
const members = ref<AdminPageOut<AdminMemberOut> | null>(null)
const membersState = ref<'loading' | 'ready' | 'error'>('loading')
const membersPage = ref(1)
const membersPageSize = 20
const memberSearch = ref('')
const memberSearchCommitted = ref('')
const memberStatusFilter = ref<'' | 'pending' | 'active' | 'rejected' | 'withdrawn' | 'removed'>('')
const selectedMember = ref<AdminMemberOut | null>(null)

async function loadSpace(): Promise<void> {
  if (!Number.isInteger(spaceId.value) || spaceId.value <= 0) {
    notFound.value = true
    state.value = 'ready'
    return
  }
  state.value = 'loading'
  try {
    space.value = await apiSpaceDetail(spaceId.value)
    state.value = 'ready'
  } catch {
    // 后端对未知空间统一 404；安全空态，不区分"不存在/无权"
    notFound.value = true
    state.value = 'ready'
  }
}

async function loadMembers(): Promise<void> {
  membersState.value = 'loading'
  try {
    members.value = await apiSpaceMembers(spaceId.value, {
      page: membersPage.value,
      pageSize: membersPageSize,
      search: memberSearchCommitted.value || null,
      status: memberStatusFilter.value || null,
    })
    membersState.value = 'ready'
  } catch {
    membersState.value = 'error'
  }
}

function submitMemberSearch(): void {
  memberSearchCommitted.value = memberSearch.value.trim()
  membersPage.value = 1
}

// 翻页/筛选/搜索统一经 watch 触发重载；同 tick 内多次变更由 watcher 批合并
watch(
  [membersPage, memberStatusFilter, memberSearchCommitted],
  () => void loadMembers(),
)

// ---- 敏感票据编排 ----
type SensitiveIntent = {
  key: 'member-profile' | 'space-relations' | 'space-facts'
  targetType: AccessTargetType
  targetId: number
  targetLabel: string
  run: () => void
}

const activeTab = ref<'members' | 'relations' | 'facts' | 'notifications'>('members')
const reasonModalVisible = ref(false)
const reasonIntent = ref<SensitiveIntent | null>(null)

const userTicket = useAccessTicket('user', () => reasonIntent.value?.targetId ?? selectedMember.value?.user_id ?? 0)
const spaceTicket = useAccessTicket('space', () => spaceId.value)

function requestSensitive(intent: SensitiveIntent): void {
  reasonIntent.value = intent
  reasonModalVisible.value = true
}

async function onReasonSubmit(reason: string): Promise<void> {
  const intent = reasonIntent.value
  if (!intent) return
  const ticket = intent.targetType === 'user' ? userTicket : spaceTicket
  const authorized = await ticket.authorize(reason)
  // 授权请求在途时目标可能已被 403 重新授权流程替换/关闭：
  // 不覆盖最新状态（否则会把刚重开的理由弹窗又关掉）
  if (reasonIntent.value !== intent) return
  if (!authorized) return
  reasonModalVisible.value = false
  intent.run()
}

function onReasonClose(): void {
  reasonModalVisible.value = false
  reasonIntent.value = null
}

// ---- 面板触发 ----

function openMemberProfile(member: AdminMemberOut): void {
  selectedMember.value = member
  if (userTicket.hasValidTicket()) return
  requestSensitive({
    key: 'member-profile',
    targetType: 'user',
    targetId: member.user_id,
    targetLabel: `成员档案：${member.name}`,
    run: () => undefined, // 面板挂载时自行加载
  })
}

function onProfileUnauthorized(): void {
  userTicket.handleRejected()
  const member = selectedMember.value
  if (!member) return
  requestSensitive({
    key: 'member-profile',
    targetType: 'user',
    targetId: member.user_id,
    targetLabel: `成员档案：${member.name}`,
    run: () => undefined,
  })
}

function switchTab(tab: 'members' | 'relations' | 'facts' | 'notifications'): void {
  activeTab.value = tab
  if (tab === 'relations' || tab === 'facts') {
    if (!spaceTicket.hasValidTicket()) {
      requestSensitive({
        key: tab === 'facts' ? 'space-facts' : 'space-relations',
        targetType: 'space',
        targetId: spaceId.value,
        targetLabel: `空间：${space.value?.name ?? `#${spaceId.value}`}`,
        run: () => undefined,
      })
    }
  }
}

function onSpaceSensitiveUnauthorized(): void {
  spaceTicket.handleRejected()
  requestSensitive({
    key: (activeTab.value === 'facts' ? 'space-facts' : 'space-relations') as SensitiveIntent['key'],
    targetType: 'space',
    targetId: spaceId.value,
    targetLabel: `空间：${space.value?.name ?? `#${spaceId.value}`}`,
    run: () => undefined,
  })
}

const reasonModalError = computed(() =>
  reasonIntent.value?.targetType === 'user' ? userTicket.ticketError.value : spaceTicket.ticketError.value,
)

const reasonModalPending = computed(() =>
  reasonIntent.value?.targetType === 'user' ? userTicket.requesting.value : spaceTicket.requesting.value,
)

onMounted(() => {
  void loadSpace()
  void loadMembers()
})

</script>

<template>
  <div>
    <nav class="ag-breadcrumb" aria-label="面包屑">
      <RouterLink
        class="ag-back-link"
        :to="{ name: 'overview' }"
        data-testid="back-to-overview"
      >
        <span aria-hidden="true">←</span>
        返回概览
      </RouterLink>
      <span>/</span>
      <span>空间详情</span>
    </nav>

    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无空间数据" @retry="loadSpace" />
    <div v-else-if="notFound" class="ag-card" data-testid="space-not-found">
      <PageState state="empty" empty-text="无法访问该空间" />
    </div>

    <template v-else-if="space">
      <h1 class="ag-page-title" data-testid="space-detail-name">{{ space.name }}</h1>
      <p class="ag-page-subtitle">
        {{ space.kind === 'household' ? '家庭空间' : '族谱空间' }} · 成员 {{ space.member_count }} 人 ·
        建于 {{ space.created_at }}
      </p>

      <section class="ag-card" aria-label="空间概要">
        <div class="ag-toolbar">
          <span class="ag-tag">
            在任管理员：
            <template v-if="space.manager_name">
              {{ space.manager_name }}（#{{ space.manager_user_id }}）
            </template>
            <template v-else>无</template>
          </span>
          <span
            v-if="space.anomalies.length > 0"
            class="ag-tag ag-tag-anomaly"
            data-testid="space-anomalies"
          >
            异常：{{ space.anomalies.join('、') }}
          </span>
          <span v-else class="ag-tag ag-tag-healthy">无异常</span>
        </div>

        <div class="ag-toolbar" role="tablist" aria-label="空间详情面板">
          <button
            type="button"
            role="tab"
            class="ag-tag ag-tab"
            :aria-selected="activeTab === 'members'"
            @click="switchTab('members')"
          >
            成员
          </button>
          <button
            type="button"
            role="tab"
            class="ag-tag ag-tab"
            :aria-selected="activeTab === 'relations'"
            data-testid="tab-relations"
            @click="switchTab('relations')"
          >
            关系
          </button>
          <button
            type="button"
            role="tab"
            class="ag-tag ag-tab"
            :aria-selected="activeTab === 'facts'"
            data-testid="tab-facts"
            @click="switchTab('facts')"
          >
            已确认事实
          </button>
          <button
            type="button"
            role="tab"
            class="ag-tag ag-tab"
            :aria-selected="activeTab === 'notifications'"
            @click="switchTab('notifications')"
          >
            通知
          </button>
        </div>

        <!-- 成员面板（普通读取 + 敏感档案钻取） -->
        <div v-if="activeTab === 'members'">
          <div class="ag-toolbar">
            <form class="ag-inline-form" @submit.prevent="submitMemberSearch">
              <input
                v-model="memberSearch"
                type="search"
                placeholder="搜索成员姓名"
                aria-label="搜索成员姓名"
                data-testid="members-search"
              />
              <button type="submit" class="ag-tag">搜索</button>
            </form>
            <label class="ag-filter-label">
              状态
              <select v-model="memberStatusFilter" aria-label="按成员状态筛选" data-testid="members-status">
                <option value="">全部</option>
                <option value="pending">待接受</option>
                <option value="active">在籍</option>
                <option value="rejected">已拒绝</option>
                <option value="withdrawn">已退出</option>
                <option value="removed">已移除</option>
              </select>
            </label>
          </div>
          <PageState
            v-if="membersState !== 'ready'"
            :state="membersState"
            empty-text="暂无成员"
            @retry="loadMembers"
          />
          <template v-else-if="members">
            <div v-if="members.items.length === 0">
              <PageState state="empty" empty-text="没有匹配的成员" />
            </div>
            <template v-else>
              <div class="ag-table-wrap">
                <table class="ag-table" data-testid="members-table">
                  <thead>
                    <tr>
                      <th>姓名</th>
                      <th>角色</th>
                      <th>状态</th>
                      <th>加入时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="member in members.items" :key="member.user_id">
                      <td>{{ member.name }}</td>
                      <td>{{ member.role === 'space_admin' ? '空间管理员' : '成员' }}</td>
                      <td>{{ member.status }}</td>
                      <td>{{ member.created_at }}</td>
                      <td>
                        <button
                          type="button"
                          class="ag-tag"
                          data-testid="open-profile-button"
                          @click="openMemberProfile(member)"
                        >
                          查看档案
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ListPagination
                :page="members.page"
                :page-size="members.page_size"
                :total="members.total"
                @update:page="membersPage = $event"
              />
            </template>
          </template>

          <MemberProfilePanel
            v-if="selectedMember && userTicket.hasValidTicket()"
            :key="selectedMember.user_id"
            :user-id="selectedMember.user_id"
            @unauthorized="onProfileUnauthorized"
            @close="selectedMember = null"
          />
        </div>

        <!-- 关系（敏感） -->
        <template v-if="activeTab === 'relations'">
          <div v-if="!spaceTicket.hasValidTicket()" class="sensitive-notice">
            查看关系需要访问授权：请说明理由后获取 30 分钟票据。
          </div>
          <SpaceRelationsPanel
            v-if="spaceTicket.hasValidTicket()"
            :key="`relations-${spaceId}`"
            :space-id="spaceId"
            @unauthorized="onSpaceSensitiveUnauthorized"
          />
        </template>

        <!-- 已确认事实（敏感） -->
        <template v-if="activeTab === 'facts'">
          <div v-if="!spaceTicket.hasValidTicket()" class="sensitive-notice">
            查看已确认事实需要访问授权：请说明理由后获取 30 分钟票据。
          </div>
          <SpaceFactsPanel
            v-if="spaceTicket.hasValidTicket()"
            :key="`facts-${spaceId}`"
            :space-id="spaceId"
            @unauthorized="onSpaceSensitiveUnauthorized"
          />
        </template>

        <!-- 通知（普通） -->
        <SpaceNotificationsPanel
          v-if="activeTab === 'notifications'"
          :space-id="spaceId"
        />
      </section>

      <ReasonModal
        :visible="reasonModalVisible"
        :target-label="reasonIntent?.targetLabel ?? ''"
        :pending="reasonModalPending"
        :error-message="reasonModalError"
        @submit="onReasonSubmit"
        @close="onReasonClose"
      />
    </template>
  </div>
</template>
