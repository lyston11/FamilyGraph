<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import { ApiError } from '@/api/errors'
import { useMembersStore } from '@/stores/members'
import type { GenderType, PrivacyMode, StructuredDate } from '@/types/api'

/**
 * 建档向导（m1a design）：三步 + 结果弹窗。
 * 步骤：资料 → 归属模式 D5 二选一 → 提交。
 * 第四步「是否加入我的空间」为 m1c 空间接入预留插槽（steps 预留，暂不渲染）。
 */
const emit = defineEmits<{ close: []; created: [{ name: string; pin: string }] }>()

const members = useMembersStore()

const step = ref(0)
const submitting = ref(false)
const errorMessage = ref('')

/** m1c 将把空间勾选作为第四步插入；当前仅三步 */
const STEP_TITLES = ['资料', '归属模式', '确认提交'] as const

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
  step.value = 2
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
      <!-- m1c 空间接入时在此追加第四步「加入我的空间」 -->
      <el-step v-for="(title, index) in STEP_TITLES" :key="index" :title="title" />
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

    <!-- 第三步：确认提交 -->
    <div v-else data-test="wizard-step-confirm">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="名字">{{ form.name }}</el-descriptions-item>
        <el-descriptions-item label="性别">
          {{ form.gender === 'f' ? '女' : form.gender === 'm' ? '男' : '不详' }}
        </el-descriptions-item>
        <el-descriptions-item label="归属模式">
          {{ form.privacyMode === 'handover' ? '移交本人' : '永久管理' }}
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
      <el-button v-if="step === 1" type="primary" data-test="wizard-to-confirm" @click="goConfirm">
        下一步
      </el-button>
      <el-button
        v-if="step === 2"
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
