<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { fetchLunarMirror } from '@/api/lunar'
import { ApiError } from '@/api/errors'
import { createSpace } from '@/api/spaces'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import type { DirClass, GenderType, PrivacyMode, StructuredDate } from '@/types/api'

/**
 * 建档向导（v2 F-1/F-3）：资料（名字+关系必填）→ 归属模式 D5 → [选择空间
 * no-space/household/lineage] → 提交。
 * 对方确档前仅为 provisional 档案：选空间只建 space_profile_refs 最小节点引用，
 * 不是正式 SpaceMember；与创建者的关系以待确认合并请求发出，本人确档后可确认。
 */
const emit = defineEmits<{ close: []; created: [{ name: string; pin: string | null }] }>()

const members = useMembersStore()
const spacesStore = useSpacesStore()

const step = ref(0)
const submitting = ref(false)
const errorMessage = ref('')
const idempotencyKey = ref('')

// ---- 关系（F-1 必填）：TA 是我的 ___；以合并请求发出，对方确档后可确认 ----
const RELATION_OPTIONS: { value: DirClass; text: string }[] = [
  { value: 'elder', text: '长辈' },
  { value: 'younger', text: '晚辈' },
  { value: 'peer', text: '平辈' },
  { value: 'spouse', text: '配偶' },
]
const relationDir = ref<DirClass | ''>('')
const relationLabel = ref('')

// ---- 空间选择（F-3）：no-space / household / lineage ----
type SpaceChoice = 'none' | 'household' | 'lineage'
const spaceChoice = ref<SpaceChoice>('none')
const joinHouseholdSpaceId = ref<number | null>(null)
const joinLineageSpaceId = ref<number | null>(null)
const newLineageName = ref('')
const creatingLineage = ref(false)

onMounted(async () => {
  try {
    await spacesStore.load()
  } catch {
    /* 空间加载失败不阻塞建档 */
  }
})

/** 有任一空间时插入「选择空间」步骤（F-3） */
const hasSpaces = computed(() => spacesStore.spaces.length > 0)
const householdSpaces = computed(() => spacesStore.spaces.filter((s) => s.kind === 'household'))
const lineageSpaces = computed(() => spacesStore.spaces.filter((s) => s.kind === 'lineage'))
const stepTitles = computed(() =>
  hasSpaces.value
    ? (['资料', '归属模式', '选择空间', '确认提交'] as const)
    : (['资料', '归属模式', '确认提交'] as const),
)
const confirmStep = computed(() => (hasSpaces.value ? 3 : 2))

/** 最终选定的空间引用（null = 不加入任何空间） */
const chosenSpaceId = computed<number | null>(() => {
  if (!hasSpaces.value || spaceChoice.value === 'none') return null
  return spaceChoice.value === 'household' ? joinHouseholdSpaceId.value : joinLineageSpaceId.value
})

/** 选择空间步骤的合法性：选中类别必须有具体空间 */
const spaceSelectionValid = computed(
  () => !hasSpaces.value || spaceChoice.value === 'none' || chosenSpaceId.value !== null,
)

const form = reactive({
  name: '',
  gender: 'unknown' as GenderType,
  birthCalType: 'solar' as StructuredDate['cal_type'],
  birthDate: '',
  birthMirror: '' as string | null,
  deathEnabled: false,
  deathCalType: 'solar' as StructuredDate['cal_type'],
  deathDate: '',
  bio: '',
  privacyMode: 'handover' as PrivacyMode,
})

/** m3b：历别切换自动换算互填（后端 lunar-python 单一实现，避免前端双写） */
type SolarOrLunar = 'solar' | 'lunar'

async function fetchMirror(calType: SolarOrLunar, date: string): Promise<string | null> {
  return await fetchLunarMirror(calType, date).catch(() => null)
}

let prevBirthCal: SolarOrLunar | 'none' = 'solar'
watch(
  () => form.birthCalType,
  async (newType) => {
    const typed = newType as StructuredDate['cal_type']
    const oldType = prevBirthCal
    prevBirthCal = typed
    void onBirthCalChange(typed, oldType)
  },
)

async function onBirthCalChange(newType: StructuredDate['cal_type'], oldType?: StructuredDate['cal_type']) {
  if (newType === oldType) return
  if ((oldType === 'solar' || oldType === 'lunar') && (newType === 'solar' || newType === 'lunar')) {
    // 双历间切换：用旧历日期的镜像作为新历预填（可撤销——再次切回即还原）
    const dateStr = String(form.birthDate || '')
    if (!dateStr) return
    const mirror = await fetchMirror(oldType, dateStr)
    if (mirror) {
      const [y, rest] = [mirror.split(':')[0], mirror.split(':').slice(1).join('-')]
      const normalized = rest.startsWith('-')
        ? `${y}-${String(Math.abs(Number(rest.split('-')[0]))).padStart(2, '0')}-${rest.split('-')[1]}`
        : `${y}-${rest}`
      form.birthDate = normalized
      form.birthMirror = mirror
    }
  }
}

const canNextFromInfo = computed(() => form.name.trim().length > 0 && relationDir.value !== '')

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
  if (!canNextFromInfo.value) return
  // 有空间时先经过「选择空间」步骤（F-3），再进入确认
  if (hasSpaces.value && step.value === 1) {
    errorMessage.value = ''
    step.value = 2
    return
  }
  if (hasSpaces.value && step.value === 2 && !spaceSelectionValid.value) {
    errorMessage.value = '请先选择一个具体空间，或选择不加入'
    return
  }
  errorMessage.value = ''
  step.value = confirmStep.value
}

async function createLineageSpace(): Promise<void> {
  const name = newLineageName.value.trim()
  if (!name) return
  creatingLineage.value = true
  try {
    const space = await createSpace(name, 'lineage')
    spacesStore.spaces.unshift(space)
    joinLineageSpaceId.value = space.id
    newLineageName.value = ''
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '创建族谱空间失败')
  } finally {
    creatingLineage.value = false
  }
}

async function submit(): Promise<void> {
  submitting.value = true
  errorMessage.value = ''
  try {
    const result = await members.create(
      {
        name: form.name.trim(),
        gender: form.gender,
        birth: buildStructuredDate(form.birthCalType, form.birthDate),
        death: form.deathEnabled
          ? buildStructuredDate(form.deathCalType, form.deathDate)
          : null,
        bio: form.bio.trim() || null,
        privacy_mode: form.privacyMode,
        space_membership:
          chosenSpaceId.value !== null ? { space_id: chosenSpaceId.value } : null,
        relation_dir_class: relationDir.value as DirClass,
        relation_label: relationLabel.value.trim() || null,
      },
      ensureIdempotencyKey(),
    )
    // 档案 + 关系已原子提交；重放时不再回放一次性 PIN（仅首次可见）
    emit('created', { name: result.user.name, pin: result.pin })
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '建档失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

function ensureIdempotencyKey(): string {
  if (!idempotencyKey.value) {
    idempotencyKey.value = crypto.randomUUID()
  }
  return idempotencyKey.value
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
  relationDir.value = ''
  relationLabel.value = ''
  idempotencyKey.value = ''
  spaceChoice.value = 'none'
  joinHouseholdSpaceId.value = null
  joinLineageSpaceId.value = null
  newLineageName.value = ''
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

    <!-- 第一步：资料（F-1：名字与关系必填） -->
    <el-form v-if="step === 0" label-position="top" data-test="wizard-step-info">
      <el-form-item label="名字（允许重名）" required>
        <el-input
          v-model="form.name"
          maxlength="100"
          placeholder="例如：王秀英"
          data-test="wizard-name"
        />
      </el-form-item>
      <el-form-item label="与你的关系" required>
        <div class="relation-block">
          <el-radio-group v-model="relationDir" data-test="wizard-relation-dir">
            <el-radio v-for="opt in RELATION_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.text }}
            </el-radio>
          </el-radio-group>
          <el-input
            v-model="relationLabel"
            class="relation-label-input"
            maxlength="64"
            placeholder="称谓选填，如：三叔公"
            data-test="wizard-relation-label"
          />
          <div class="confirm-hint">
            新建档案为 provisional，由你代管：关系将在建档时直接建立，对方确档后可再确认与修正。
          </div>
        </div>
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

    <!-- 第三步（有空间时）：选择空间（F-3：no-space / household / lineage） -->
    <el-form v-else-if="hasSpaces && step === 2" label-position="top" data-test="wizard-step-space">
      <el-form-item label="加入哪个空间？">
        <el-radio-group v-model="spaceChoice" data-test="wizard-space-choice">
          <el-radio value="none">不加入空间</el-radio>
          <el-radio value="household" :disabled="householdSpaces.length === 0">家庭空间</el-radio>
          <el-radio value="lineage">族谱空间</el-radio>
        </el-radio-group>
      </el-form-item>
      <template v-if="spaceChoice === 'household'">
        <el-form-item>
          <el-select
            v-model="joinHouseholdSpaceId"
            placeholder="选择家庭空间"
            data-test="wizard-household-select"
            style="width: 100%"
          >
            <el-option
              v-for="space in householdSpaces"
              :key="space.id"
              :label="space.name"
              :value="space.id"
            />
          </el-select>
        </el-form-item>
      </template>
      <template v-if="spaceChoice === 'lineage'">
        <el-form-item>
          <el-select
            v-model="joinLineageSpaceId"
            placeholder="选择族谱空间"
            data-test="wizard-lineage-select"
            style="width: 100%"
          >
            <el-option
              v-for="space in lineageSpaces"
              :key="space.id"
              :label="space.name"
              :value="space.id"
            />
          </el-select>
          <div class="lineage-create">
            <el-input
              v-model="newLineageName"
              placeholder="或新建族谱空间名"
              maxlength="64"
              data-test="wizard-lineage-name"
            />
            <el-button
              size="small"
              :loading="creatingLineage"
              :disabled="!newLineageName.trim()"
              data-test="wizard-lineage-create"
              @click="createLineageSpace"
            >
              新建
            </el-button>
          </div>
        </el-form-item>
      </template>
      <p class="confirm-hint">
        对方完成确档前，仅以最小节点引用出现在所选空间，不是正式空间成员。
      </p>
    </el-form>

    <!-- 确认提交 -->
    <div v-else data-test="wizard-step-confirm">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="名字">{{ form.name }}</el-descriptions-item>
        <el-descriptions-item label="与你的关系">
          {{ RELATION_OPTIONS.find((o) => o.value === relationDir)?.text ?? '—' }}
          {{ relationLabel.trim() ? `（${relationLabel.trim()}）` : '' }}
        </el-descriptions-item>
        <el-descriptions-item label="性别">
          {{ form.gender === 'f' ? '女' : form.gender === 'm' ? '男' : '不详' }}
        </el-descriptions-item>
        <el-descriptions-item label="归属模式">
          {{ form.privacyMode === 'handover' ? '移交本人' : '永久管理' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="chosenSpaceId" label="加入空间">
          {{ spacesStore.spaces.find((s) => s.id === chosenSpaceId)?.name }}
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

.relation-block {
  width: 100%;
}

.relation-label-input {
  margin-top: 8px;
}

.lineage-create {
  display: flex;
  gap: 8px;
  margin-top: 8px;
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
