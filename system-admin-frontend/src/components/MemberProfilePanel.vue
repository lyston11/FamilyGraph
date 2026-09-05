<script setup lang="ts">
/**
 * 成员基础档案面板（敏感：user 访问会话；no-store）。
 * 字段白名单 = AdminProfileOut（无 updated_at / avatar_path / 凭据）；
 * 头像为鉴权缩略图（blob URL，卸载即 revoke）；附件仅安全元数据，无下载入口。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  apiUserAttachments,
  apiUserAvatarThumbnail,
  apiUserProfile,
} from '@/api/read'
import { AccessSessionInvalidError } from '@/api/client'
import PageState from '@/components/PageState.vue'
import ListPagination from '@/components/ListPagination.vue'
import type {
  AdminAttachmentMetadataOut,
  AdminPageOut,
  AdminProfileOut,
} from '@/types/api'

const props = defineProps<{ userId: number }>()

const emit = defineEmits<{ unauthorized: []; close: [] }>()

const profile = ref<AdminProfileOut | null>(null)
const attachments = ref<AdminPageOut<AdminAttachmentMetadataOut> | null>(null)
const avatarUrl = ref<string | null>(null)
const state = ref<'loading' | 'ready' | 'error'>('loading')
const attachmentPage = ref(1)
const attachmentPageSize = 10

let currentAvatarUrl: string | null = null

function releaseAvatar(): void {
  if (currentAvatarUrl !== null) {
    URL.revokeObjectURL(currentAvatarUrl)
    currentAvatarUrl = null
    avatarUrl.value = null
  }
}

function describeDate(value: AdminProfileOut['birth'], label: string): string {
  if (!value) return `${label}：未知`
  const text = value.date ?? value.mirror_date ?? ''
  if (!text) return `${label}：未知`
  const cal = value.cal_type === 'lunar' ? '农历' : '公历'
  return `${label}：${text}（${cal}）`
}

async function load(): Promise<void> {
  state.value = 'loading'
  releaseAvatar()
  try {
    const [profileOut, attachmentsOut] = await Promise.all([
      apiUserProfile(props.userId),
      apiUserAttachments(props.userId, { page: attachmentPage.value, pageSize: attachmentPageSize }),
    ])
    profile.value = profileOut
    attachments.value = attachmentsOut
    state.value = 'ready'
    if (profileOut.avatar_available) {
      // 缩略图必须带票据请求（<img> 无法带 header → 取 blob 再生成内存 URL）；
      // 头像失败不影响档案主体展示（降级为"无头像"）。
      try {
        const blob = await apiUserAvatarThumbnail(props.userId)
        currentAvatarUrl = URL.createObjectURL(blob)
        avatarUrl.value = currentAvatarUrl
      } catch {
        releaseAvatar()
      }
    }
  } catch (error) {
    if (error instanceof AccessSessionInvalidError) {
      emit('unauthorized')
      return
    }
    state.value = 'error'
  }
}

watch(
  attachmentPage,
  () => {
    void reloadAttachments()
  },
)

async function reloadAttachments(): Promise<void> {
  if (profile.value === null) return
  try {
    attachments.value = await apiUserAttachments(props.userId, {
      page: attachmentPage.value,
      pageSize: attachmentPageSize,
    })
  } catch (error) {
    if (error instanceof AccessSessionInvalidError) {
      emit('unauthorized')
    }
  }
}

onMounted(load)
onBeforeUnmount(releaseAvatar)
</script>

<template>
  <section class="ag-card" data-testid="member-profile-panel" aria-label="成员档案详情">
    <div class="ag-toolbar">
      <h2 class="ag-page-title ag-heading-sm ag-m-0">成员档案</h2>
      <button type="button" class="ag-tag" @click="$emit('close')">关闭</button>
    </div>

    <PageState v-if="state !== 'ready'" :state="state" empty-text="暂无档案数据" @retry="load" />
    <template v-else-if="profile">
      <div class="ag-toolbar">
        <div v-if="avatarUrl" class="ag-avatar">
          <img
            :src="avatarUrl"
            alt="成员头像缩略图"
            class="ag-avatar-img"
            data-testid="member-avatar"
          />
        </div>
        <span v-else class="ag-tag">无头像</span>
        <div>
          <div class="ag-profile-name" data-testid="member-profile-name">{{ profile.name }}</div>
          <div class="ag-card-row-meta">
            档案 #{{ profile.id }} ·
            {{ profile.profile_status === 'identity_confirmed' ? '身份已确认' : '身份待确认' }} ·
            {{ profile.claim_status === 'claimed' ? '账号已认领' : '账号待认领' }}
          </div>
        </div>
      </div>
      <ul class="ag-profile-list">
        <li>性别：{{ profile.gender }}</li>
        <li>{{ describeDate(profile.birth, '出生') }}</li>
        <li>{{ describeDate(profile.death, '逝世') }}</li>
        <li v-if="profile.bio">简介：{{ profile.bio }}</li>
        <li>建档时间：{{ profile.created_at }}</li>
      </ul>

      <h3 class="ag-page-title ag-heading-section">附件元数据</h3>
      <p class="ag-page-subtitle ag-sub-flush">
        仅展示安全元数据；附件内容与存储路径不提供访问。
      </p>
      <PageState
        v-if="attachments && attachments.items.length === 0"
        state="empty"
        empty-text="该成员暂无附件"
      />
      <template v-else-if="attachments && attachments.items.length > 0">
        <div class="ag-table-wrap">
          <table class="ag-table" data-testid="member-attachments-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>类型</th>
                <th>标题</th>
                <th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in attachments.items" :key="item.id">
                <td>{{ item.id }}</td>
                <td>{{ item.type }}</td>
                <td>{{ item.title_safe ?? '-' }}</td>
                <td>{{ item.created_at }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ListPagination
          :page="attachments.page"
          :page-size="attachments.page_size"
          :total="attachments.total"
          @update:page="attachmentPage = $event"
        />
      </template>
    </template>
  </section>
</template>
