<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { ApiError } from '@/api/errors'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import type { GenderType, PrivacyMode, StructuredDate } from '@/types/api'

/**
 * 建档向导（m1a design）：资料 → 归属模式 D5 二选一 → [加入空间(m1c，有空间时)] → 提交。
 */
const emit = defineEmits<{ close: []; created: [{ name: string; pin: string }] }>()

const members = useMembersStore()
const spacesStore = useSpacesStore()

const step = ref(0)
const submitting = ref(false)
const errorMessage = ref('')
const joinSpaceEnabled = ref(true)
const joinSpaceId = ref<number | null>(null)

onMounted(async () => {
  try {
    await spacesStore.load()
    joinSpaceId.value = spacesStore.spaces[0]?.id ?? null
  } catch {
    /* 空间加载失败不阻塞建档 */
  }
})

/** 有空间时插入「加入我的空间」步骤（m1c） */
const hasSpaces = computed(() => spacesStore.spaces.length > 0)
const stepTitles = computed(() =>
  hasSpaces.value
    ? (['资料', '归属模式', '加入空间', '确认提交'] as const)
    : (['资料', '归属模式', '确认提交'] as const),
)
const confirmStep = computed(() => (hasSpaces.value ? 3 : 2))

const form = reactive({
  name: '',
  gender: 'unknown' as GenderType,
  birthCalType: 'solar' as StructuredDate['cal_type'],
  birthDate: '',
  deathEnabled: false,
  deathCalType: 'solar' as StructuredDate['cal_type'],
  deathDate: '',
  bio: '',
  privacyMode: 'handover' as PrivacyMode,
})

const canNextFromInfo = computed(() => form.name.trim().length > 0)

function buildStructuredDate(calType: StructuredDate['cal_type'], raw: string): StructuredDate | null {
  if (calType === 'none') {
    return { cal_type: 'none', date: null }
  }
  return { cal_type: calType, date: raw || null }
}

function goInfo(): void {
  step.value = 0
}

function goMode(): void {
  if (!canNextFromInfo.value) return
  errorMessage.value = ''
  step.value = 1
}

function goConfirm(): void {
  step.value = confirmStep.value
}

async function submit(): Promise<void> {
  submitting.value = true
  errorMessage.value = ''
  try {
    const result = await members.create({
      name: form.name.trim(),
      gender: form.gender,
      birth: buildStructuredDate(form.birthCalType, form.birthDate),
      death: form.deathEnabled
        ? buildStructuredDate(form.deathCalType, form.deathDate)
        : null,
      bio: form.bio.trim() || null,
      privacy_mode: form.privacyMode,
      space_membership:
        hasSpaces.value && joinSpaceEnabled.value && joinSpaceId.value !== null
          ? { space_id: joinSpaceId.value }
          : null,
    })
    emit('created', { name: result.user.name, pin: result.pin })
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '建档失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function reset(): void {
  step.value = 0
  errorMessage.value = ''
  form.name = ''
  form.gender = 'unknown'
  form.birthCalType = 'solar'
  form.birthDate = ''
  form.deathEnabled = false
  form.deathCalType = 'solar'
  form.deathDate = ''
  form.bio = ''
  form.privacyMode = 'handover'
  joinSpaceEnabled.value = true
}

function handleClose(): void {
  reset()
  emit('close')
}
</script>

<template>
  <el-dialog
    :model-value="true"
    title="添加家人"
    width="520px"
    align-center
    :close-on-click-modal="false"
    data-test="wizard-dialog"
    @update:model-value="handleClose()"
  >
    <el-steps :active="step" align-center finish-status="success" class="steps">
      <el-step v-for="(title, index) in stepTitles" :key="index" :title="title" />
    </el-steps>

    <!-- 第一步：资料 -->
    <el-form v-if="step === 0" label-position="top" data-test="wizard-step-info">
      <el-form-item label="名字（允许重名）" required>
        <el-input
          v-model="form.name"
          maxlength="100"
          placeholder="例如：王秀英"
          data-test="wizard-name"
        />
      </el-form-item>
      <el-form-item label="性别">
        <el-radio-group v-model="form.gender" data-test="wizard-gender">
          <el-radio value="f">女</el-radio>
          <el-radio value="m">男</el-radio>
          <el-radio value="unknown">不详</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="出生">
        <div class="date-row">
          <el-select v-model="form.birthCalType" class="cal-select" data-test="wizard-birth-cal">
            <el-option label="公历" value="solar" />
            <el-option label="农历" value="lunar" />
            <el-option label="不详" value="none" />
          </el-select>
          <el-date-picker
            v-if="form.birthCalType !== 'none'"
            v-model="form.birthDate"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            data-test="wizard-birth-date"
          />
        </div>
        <!-- 历别换算（公⇄农历自动互补）由 m1d 接入，此处先记录录入原文 -->
      </el-form-item>
      <el-form-item label="去世">
        <div class="date-row">
          <el-checkbox v-model="form.deathEnabled" data-test="wizard-death-enable">已去世</el-checkbox>
          <template v-if="form.deathEnabled">
            <el-select v-model="form.deathCalType" class="cal-select" data-test="wizard-death-cal">
              <el-option label="公历" value="solar" />
              <el-option label="农历" value="lunar" />
              <el-option label="不详" value="none" />
            </el-select>
            <el-date-picker
              v-if="form.deathCalType !== 'none'"
              v-model="form.deathDate"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="选择日期"
              data-test="wizard-death-date"
            />
          </template>
        </div>
      </el-form-item>
      <el-form-item label="简介">
        <el-input
          v-model="form.bio"
          type="textarea"
          :rows="2"
          maxlength="2000"
          placeholder="选填"
          data-test="wizard-bio"
        />
      </el-form-item>
    </el-form>

    <!-- 第二步：归属模式（D5 二选一） -->
    <el-form v-else-if="step === 1" label-position="top" data-test="wizard-step-mode">
      <el-form-item label="这份档案归谁维护？">
        <el-radio-group v-model="form.privacyMode" class="mode-group" data-test="wizard-mode">
          <el-radio value="handover" border class="mode-option">
            <span class="mode-title">移交本人</span>
            <span class="mode-desc">亲人领取账号后获得全部编辑权，你转为只读。</span>
          </el-radio>
          <el-radio value="perpetual" border class="mode-option">
            <span class="mode-title">永久管理</span>
            <span class="mode-desc">无论对方是否领取账号，你都保留编辑权（适合已故亲人）。</span>
          </el-radio>
        </el-radio-group>
      </el-form-item>
    </el-form>

    <!-- 第三步（有空间时）：加入我的空间（AD-4 新建例外：直接 active） -->
    <el-form v-else-if="hasSpaces && step === 2" label-position="top" data-test="wizard-step-space">
      <el-form-item>
        <el-checkbox v-model="joinSpaceEnabled" data-test="wizard-space-enable">
          同时加入我的家庭空间
        </el-checkbox>
      </el-form-item>
      <el-form-item v-if="joinSpaceEnabled">
        <el-select
          v-model="joinSpaceId"
          placeholder="选择空间"
          data-test="wizard-space-select"
          style="width: 100%"
        >
          <el-option
            v-for="space in spacesStore.spaces"
            :key="space.id"
            :label="space.name"
            :value="space.id"
          />
        </el-select>
        <div class="confirm-hint">新建档案将直接进入该空间（你是代管人）。</div>
      </el-form-item>
    </el-form>

    <!-- 确认提交 -->
    <div v-else data-test="wizard-step-confirm">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="名字">{{ form.name }}</el-descriptions-item>
        <el-descriptions-item label="性别">
          {{ form.gender === 'f' ? '女' : form.gender === 'm' ? '男' : '不详' }}
        </el-descriptions-item>
        <el-descriptions-item label="归属模式">
          {{ form.privacyMode === 'handover' ? '移交本人' : '永久管理' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="hasSpaces && joinSpaceEnabled && joinSpaceId" label="加入空间">
          {{ spacesStore.spaces.find((s) => s.id === joinSpaceId)?.name }}
        </el-descriptions-item>
      </el-descriptions>
      <p class="confirm-hint">提交后系统将生成一次性 PIN 码，请转交给这位家人。</p>
    </div>

    <p v-if="errorMessage" class="error" data-test="wizard-error">{{ errorMessage }}</p>

    <template #footer>
      <el-button v-if="step > 0" data-test="wizard-prev" @click="goInfo">上一步</el-button>
      <el-button
        v-if="step === 0"
        type="primary"
        :disabled="!canNextFromInfo"
        data-test="wizard-next"
        @click="goMode"
      >
        下一步
      </el-button>
      <el-button
        v-if="step === 1 || (hasSpaces && step === 2)"
        type="primary"
        data-test="wizard-to-confirm"
        @click="goConfirm"
      >
        下一步
      </el-button>
      <el-button
        v-else-if="step === confirmStep"
        type="primary"
        :loading="submitting"
        data-test="wizard-submit"
        @click="submit"
      >
        创建档案并生成 PIN
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.steps {
  margin-bottom: 20px;
}

.date-row {
  display: flex;
  gap: 8px;
  align-items: center;
  width: 100%;
}

.cal-select {
  width: 110px;
}

.mode-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.mode-option {
  height: auto;
  padding: 12px 16px;
  margin-right: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

.mode-option :deep(.el-radio__label) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  white-space: normal;
}

.mode-title {
  font-weight: 600;
}

.mode-desc {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.confirm-hint {
  margin-top: 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.error {
  margin-top: 8px;
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
