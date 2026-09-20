<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NModal, NRadio, NRadioGroup, NSpin, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchHouseholdInviteOptions } from '@/api/spaces'
import { useSpacesStore } from '@/stores/spaces'
import type { HouseholdInviteOption } from '@/types/api'

/**
 * 个人公示页「邀请 TA 加入我的家庭空间」弹窗（09-20）。
 *
 * - 空间列表与目标状态由服务端裁定（`GET /spaces/household-invite-options`），
 *   本组件不做本地成员推断；
 * - 已是 active 成员或已有 pending 邀请的空间不可选，并显示对应状态文案；
 * - 确认走既有邀请命令（`POST /spaces/{id}/members`），只产生 pending：
 *   受邀人本人接受后才 active，绝不静默拉人入空间；
 * - 邀请使用**所选空间**的 id，不使用调用方的当前空间上下文。
 */
const props = defineProps<{
  visible: boolean
  targetUserId: number
  targetName: string
}>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'invited', spaceId: number): void
}>()

const spaces = useSpacesStore()
const message = useMessage()

const options = ref<HouseholdInviteOption[]>([])
const selectedSpaceId = ref<number | null>(null)
const loading = ref(false)
const submitting = ref(false)
const loadError = ref('')

const STATUS_LABELS: Record<HouseholdInviteOption['target_status'], string> = {
  active: '已在同一家庭空间中',
  pending: '已发出邀请，等待对方接受',
  none: '可以邀请',
}

function selectable(option: HouseholdInviteOption): boolean {
  return option.target_status === 'none'
}

const hasSelectable = computed(() => options.value.some(selectable))

const submitDisabled = computed(
  () => submitting.value || selectedSpaceId.value === null || !hasSelectable.value,
)

async function loadOptions(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchHouseholdInviteOptions(props.targetUserId)
    options.value = data
    selectedSpaceId.value = data.find(selectable)?.space_id ?? null
  } catch (error) {
    options.value = []
    selectedSpaceId.value = null
    loadError.value =
      error instanceof ApiError ? error.message : '空间列表加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.visible, props.targetUserId] as const,
  ([visible]) => {
    if (!visible) return
    void loadOptions()
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  const spaceId = selectedSpaceId.value
  if (spaceId === null || submitting.value) return
  submitting.value = true
  try {
    await spaces.inviteToSpace(spaceId, props.targetUserId)
    message.success('邀请已发送')
    emit('invited', spaceId)
    emit('update:visible', false)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '邀请失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

function close(): void {
  if (submitting.value) return
  emit('update:visible', false)
}
</script>

<template>
  <NModal
    :show="props.visible"
    preset="card"
    title="邀请加入家庭空间"
    data-test="invite-to-household-dialog"
    @update:show="emit('update:visible', $event)"
  >
    <p class="dialog-hint" data-test="invite-dialog-hint">
      选择要邀请「{{ props.targetName }}」加入的家庭空间；对方接受后才成为成员。
    </p>

    <NSpin v-if="loading" :show="true" size="small" data-test="invite-dialog-loading" />

    <NAlert v-else-if="loadError" type="error" :show-icon="true" data-test="invite-dialog-error">
      {{ loadError }}
      <NButton size="small" class="inline-action" @click="loadOptions">重新加载</NButton>
    </NAlert>

    <p v-else-if="options.length === 0" class="dialog-hint" data-test="invite-dialog-empty">
      你还没有家庭空间。可以先创建一个家庭空间，再邀请家人加入。
    </p>

    <template v-else>
      <NAlert
        v-if="!hasSelectable"
        type="info"
        :show-icon="true"
        data-test="invite-dialog-all-members"
      >
        「{{ props.targetName }}」已在你全部的家庭空间中。
      </NAlert>
      <NRadioGroup
        v-else
        v-model:value="selectedSpaceId"
        class="space-group"
        name="household-invite-space"
        aria-label="选择要邀请加入的家庭空间"
        data-test="invite-space-group"
      >
        <NRadio
          v-for="option in options"
          :key="option.space_id"
          :value="option.space_id"
          :disabled="!selectable(option)"
          :data-test="`invite-space-${option.space_id}`"
        >
          <span class="space-name">{{ option.space_name }}</span>
          <span
            class="space-status"
            :class="{ 'space-status--blocked': !selectable(option) }"
            :data-test="`invite-space-status-${option.space_id}`"
          >{{ STATUS_LABELS[option.target_status] }}</span>
        </NRadio>
      </NRadioGroup>
    </template>

    <template #footer>
      <div class="modal-actions">
        <NButton data-test="invite-dialog-cancel" @click="close">取消</NButton>
        <NButton
          type="primary"
          :loading="submitting"
          :disabled="submitDisabled"
          data-test="invite-dialog-submit"
          @click="submit"
        >发送邀请</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.dialog-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--fg-ink-secondary);
}

.space-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.space-name {
  margin-right: 8px;
}

.space-status {
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.space-status--blocked {
  color: var(--fg-status-proposed);
}

.inline-action {
  margin-left: 8px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
