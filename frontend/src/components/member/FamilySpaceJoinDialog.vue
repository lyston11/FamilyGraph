<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSpin,
  useMessage,
} from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchFamilySpaceOptions } from '@/api/spaces'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpaceOption, FamilySpaceOptions } from '@/types/api'

/**
 * 个人公示页「加入家庭空间」弹窗（09-20，家族空间限定）。
 *
 * 两个方向都以**当前家族空间**为信任单元：
 * - 邀请：我在此家族空间下的家庭空间 → 对方加入（状态看对方）；
 * - 申请：对方在此家族空间下的家庭空间 → 我加入（状态看我）。
 *
 * - 空间列表与状态由服务端裁定（`GET /spaces/family-space-options`），本组件不做
 *   本地成员推断；
 * - 双方不同族（`shares_lineage=false`）时两个方向都不可用，只提示走邀请码途径；
 * - 两个写操作都只产生 pending：邀请需对方本人接受，申请需该空间管理员批准；
 * - 多个候选空间时由我选择（不静默挑一个）。
 */
const props = defineProps<{
  visible: boolean
  lineageSpaceId: number
  targetUserId: number
  targetName: string
}>()
const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'done', direction: 'invite' | 'join'): void
}>()

const spaces = useSpacesStore()
const message = useMessage()

type Direction = 'invite' | 'join'

const options = ref<FamilySpaceOptions | null>(null)
const direction = ref<Direction>('invite')
const selectedInviteId = ref<number | null>(null)
const selectedJoinId = ref<number | null>(null)
const loading = ref(false)
const submitting = ref(false)
const loadError = ref('')
/** 与对方的关系词（自由文本，必填，≤64）——加入空间时必须说清是什么关系 */
const relationLabel = ref('')

const STATUS_LABELS: Record<FamilySpaceOption['status'], string> = {
  active: '已在该家庭空间中',
  pending: '已有待处理的申请或邀请',
  none: '可以',
}

function available(option: FamilySpaceOption): boolean {
  return option.status === 'none'
}

function candidates(forDirection: Direction): FamilySpaceOption[] {
  return options.value === null ? [] : options.value[forDirection]
}

function firstAvailable(forDirection: Direction): number | null {
  return candidates(forDirection).find(available)?.space_id ?? null
}

const hasInvite = computed(() => candidates('invite').length > 0)
const hasJoin = computed(() => candidates('join').length > 0)
const activeList = computed(() => candidates(direction.value))
const activeSelectedId = computed(() =>
  direction.value === 'invite' ? selectedInviteId.value : selectedJoinId.value,
)

const submitDisabled = computed(
  () =>
    submitting.value ||
    options.value === null ||
    !options.value.shares_lineage ||
    activeSelectedId.value === null,
)

async function loadOptions(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchFamilySpaceOptions(props.lineageSpaceId, props.targetUserId)
    options.value = data
    selectedInviteId.value = firstAvailable('invite')
    selectedJoinId.value = firstAvailable('join')
    // 默认落在有可操作空间的方向
    direction.value = selectedInviteId.value !== null ? 'invite' : 'join'
  } catch (error) {
    options.value = null
    selectedInviteId.value = null
    selectedJoinId.value = null
    loadError.value = error instanceof ApiError ? error.message : '空间列表加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.visible, props.targetUserId, props.lineageSpaceId] as const,
  ([visible]) => {
    if (!visible) return
    void loadOptions()
  },
  { immediate: true },
)

function selectSpace(spaceId: number): void {
  if (direction.value === 'invite') selectedInviteId.value = spaceId
  else selectedJoinId.value = spaceId
}

async function submit(): Promise<void> {
  const spaceId = activeSelectedId.value
  if (spaceId === null || submitting.value || options.value === null) return
  submitting.value = true
  try {
    const label = relationLabel.value.trim()
    if (!label) {
      message.warning('请先填写你与对方的关系')
      submitting.value = false
      return
    }
    if (direction.value === 'invite') {
      await spaces.inviteIntoFamilyHousehold(
        props.lineageSpaceId,
        spaceId,
        props.targetUserId,
        label,
      )
      message.success('邀请已发送，等待房主批准与对方接受')
    } else {
      await spaces.requestJoinInFamily(
        props.lineageSpaceId,
        props.targetUserId,
        label,
        spaceId,
      )
      message.success('加入申请已发送，等待房主批准')
    }
    emit('done', direction.value)
    emit('update:visible', false)
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
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
    title="加入家庭空间"
    data-test="family-space-join-dialog"
    @update:show="emit('update:visible', $event)"
  >
    <NSpin v-if="loading" :show="true" size="small" data-test="join-dialog-loading" />

    <NAlert v-else-if="loadError" type="error" :show-icon="true" data-test="join-dialog-error">
      {{ loadError }}
      <NButton size="small" class="inline-action" @click="loadOptions">重新加载</NButton>
    </NAlert>

    <template v-else-if="options !== null">
      <!-- 不同族：两个方向都不可用，只能走邀请码 -->
      <NAlert
        v-if="!options.shares_lineage"
        type="info"
        :show-icon="true"
        data-test="join-dialog-not-same-lineage"
      >
        「{{ props.targetName }}」不在你当前的家族空间「{{ options.lineage_space_name }}」中。
        同一家族空间内的成员之间才能互相加入家庭空间；不同家族请使用邀请码。
      </NAlert>

      <template v-else>
        <p class="dialog-hint" data-test="join-dialog-hint">
          当前家族空间：{{ options.lineage_space_name }}。两个方向都只产生待确认的申请。
        </p>

        <NRadioGroup
          v-model:value="direction"
          class="direction-group"
          name="join-direction"
          aria-label="选择操作方向"
          data-test="join-direction-group"
        >
          <NRadio value="invite" :disabled="!hasInvite" data-test="join-direction-invite">
            邀请 TA 加入我的家庭空间
          </NRadio>
          <NRadio value="join" :disabled="!hasJoin" data-test="join-direction-join">
            申请加入 TA 的家庭空间
          </NRadio>
        </NRadioGroup>

        <NAlert
          v-if="activeList.length === 0"
          type="info"
          :show-icon="true"
          data-test="join-dialog-no-space"
        >
          {{
            direction === 'invite'
              ? '你在当前家族空间下还没有家庭空间，无法邀请对方加入。'
              : '对方在当前家族空间下还没有家庭空间，无法申请加入。'
          }}
        </NAlert>

        <template v-else>
          <div class="label-field">
            <label class="label-caption" for="join-relation-label">你与对方的关系</label>
            <NInput
              id="join-relation-label"
              v-model:value="relationLabel"
              placeholder="如：堂弟、表姐、朋友"
              :maxlength="64"
              data-test="join-relation-label"
            />
          </div>
          <NAlert
            v-if="!activeList.some(available)"
            type="info"
            :show-icon="true"
            data-test="join-dialog-all-taken"
          >
            {{
              direction === 'invite'
                ? `「${props.targetName}」已在你当前家族空间下的全部家庭空间中。`
                : '你已在对方当前家族空间下的全部家庭空间中。'
            }}
          </NAlert>
          <NRadioGroup
            v-else
            :value="activeSelectedId"
            class="space-group"
            name="family-space-choice"
            aria-label="选择家庭空间"
            data-test="join-space-group"
            @update:value="selectSpace"
          >
            <NRadio
              v-for="option in activeList"
              :key="option.space_id"
              :value="option.space_id"
              :disabled="!available(option)"
              :data-test="`join-space-${option.space_id}`"
            >
              <span class="space-name">{{ option.space_name }}</span>
              <span
                class="space-status"
                :class="{ 'space-status--blocked': !available(option) }"
                :data-test="`join-space-status-${option.space_id}`"
              >{{ STATUS_LABELS[option.status] }}</span>
            </NRadio>
          </NRadioGroup>
        </template>
      </template>
    </template>

    <template #footer>
      <div class="modal-actions">
        <NButton data-test="join-dialog-cancel" @click="close">取消</NButton>
        <NButton
          type="primary"
          :loading="submitting"
          :disabled="submitDisabled"
          data-test="join-dialog-submit"
          @click="submit"
        >{{ direction === 'invite' ? '发送邀请' : '发送加入申请' }}</NButton>
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

.direction-group,
.space-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.direction-group {
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--fg-glass-border);
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

.label-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}

.label-caption {
  font-size: 13px;
  color: var(--fg-ink-secondary);
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
