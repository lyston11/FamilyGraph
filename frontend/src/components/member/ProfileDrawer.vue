<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  NButton,
  NDatePicker,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSwitch,
  useMessage,
} from 'naive-ui'
import type { InputHTMLAttributes } from 'vue'

import { ApiError } from '@/api/errors'
import AttachmentsSection from '@/components/member/AttachmentsSection.vue'
import KinshipTermPanel from '@/components/kinship/KinshipTermPanel.vue'
import { useMembersStore } from '@/stores/members'
import type { ClanDisclosure, GenderType, StructuredDate } from '@/types/api'

/**
 * 档案抽屉：查看 / 按权编辑 / 披露开关组（AD-9）/ 删除输入名字确认。
 * 编辑与删除入口由后端返回的 permissions 控制，无权态直接隐藏操作。
 * 视觉：分节卡（基本信息 / 称谓关系 / 披露 / 危险区 / 附件）；领域状态徽章
 * 复用 --fg-status-* 模式（design.md §3.4）。
 */
const props = defineProps<{ memberId: number }>()

const emit = defineEmits<{ close: [] }>()

const members = useMembersStore()
const message = useMessage()

const member = computed(() =>
  members.members.find((candidate) => candidate.id === props.memberId),
)

const editing = ref(false)
const savingEdit = ref(false)
const editError = ref('')
const editForm = reactive({
  name: '',
  gender: 'unknown' as GenderType,
  birthCalType: 'solar' as StructuredDate['cal_type'],
  birthDate: '',
  bio: '',
})

// 披露开关本地副本：保存成功前不动 store
const disclosureDraft = reactive<ClanDisclosure>({
  avatar: false,
  photos: false,
  dates: false,
  bio: false,
  attachments: false,
})
const savingDisclosure = ref(false)

watch(
  member,
  (value) => {
    if (!value) return
    Object.assign(disclosureDraft, value.clan_disclosure)
    editing.value = false
    editError.value = ''
  },
  { immediate: true },
)

function startEdit(): void {
  if (!member.value) return
  editForm.name = member.value.name
  editForm.gender = member.value.gender
  editForm.birthCalType = member.value.birth?.cal_type ?? 'none'
  editForm.birthDate = member.value.birth?.date ?? ''
  editForm.bio = member.value.bio ?? ''
  editing.value = true
}

function buildBirth(): StructuredDate | null {
  if (editForm.birthCalType === 'none') {
    return { cal_type: 'none', date: null }
  }
  return { cal_type: editForm.birthCalType, date: editForm.birthDate || null }
}

async function saveEdit(): Promise<void> {
  if (!member.value) return
  if (!editForm.name.trim()) {
    editError.value = '名字不能为空'
    return
  }
  savingEdit.value = true
  editError.value = ''
  try {
    await members.update(member.value.id, {
      name: editForm.name.trim(),
      gender: editForm.gender,
      birth: buildBirth(),
      bio: editForm.bio.trim() || null,
    })
    message.success('档案已更新')
    editing.value = false
  } catch (error) {
    editError.value = error instanceof ApiError ? error.message : '保存失败，请稍后重试'
  } finally {
    savingEdit.value = false
  }
}

async function saveDisclosure(): Promise<void> {
  if (!member.value) return
  savingDisclosure.value = true
  try {
    await members.setDisclosure(member.value.id, { ...disclosureDraft })
    message.success('披露设置已更新')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    savingDisclosure.value = false
  }
}

// ---- 删除确认流 ----

const deleteDialogVisible = ref(false)
const confirmName = ref('')
const deleting = ref(false)
const deleteError = ref('')

// data-* 未收录进 Vue 的 HTML 属性类型，断言收窄；运行时 naive 原样透传到原生 input
const confirmNameInputProps = {
  'data-test': 'delete-confirm-input',
} as InputHTMLAttributes

function askDelete(): void {
  confirmName.value = ''
  deleteError.value = ''
  deleteDialogVisible.value = true
}

function onDrawerShowChange(show: boolean): void {
  if (!show) emit('close')
}

async function doDelete(): Promise<void> {
  if (!member.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await members.remove(member.value.id, confirmName.value)
    message.success('档案已删除')
    deleteDialogVisible.value = false
    emit('close')
  } catch (error) {
    if (error instanceof ApiError && error.code === 'OWNER_TRANSFER_REQUIRED') {
      // AC-F5：owner 删除被义务预检拦截 → 引导移交而非裸报错
      deleteError.value =
        '该档案名下还有家庭空间。请先登录该空间，在「空间管理」中把所有权移交给其他成员，再回来删除。'
    } else {
      deleteError.value = error instanceof ApiError ? error.message : '删除失败，请稍后重试'
    }
  } finally {
    deleting.value = false
  }
}

function genderLabel(value: GenderType): string {
  return value === 'f' ? '女' : value === 'm' ? '男' : '不详'
}

function calPrefix(calType: StructuredDate['cal_type'] | undefined): string {
  if (calType === 'lunar') return '农历 '
  if (calType === 'solar') return '公历 '
  return ''
}

function formatDate(value: StructuredDate | null): string {
  if (!value) return '不详'
  const prefix = calPrefix(value.cal_type)
  return value.date ? `${prefix}${value.date}` : '不详'
}

const calOptions = [
  { label: '公历', value: 'solar' },
  { label: '农历', value: 'lunar' },
  { label: '不详', value: 'none' },
]
</script>

<template>
  <NDrawer
    :show="true"
    placement="right"
    :width="420"
    data-test="profile-drawer"
    @update:show="onDrawerShowChange"
  >
    <template v-if="member">
      <div class="badges" data-test="drawer-badges">
        <!-- 认领状态：claimed=已确档实底 / managed=待确档虚线章（--fg-status-*） -->
        <span
          class="fg-badge"
          :class="member.claim_status === 'claimed' ? 'fg-badge--confirmed' : 'fg-badge--provisional'"
        >
          {{ member.claim_status === 'claimed' ? '已确档' : '待确档' }}
        </span>
        <span class="fg-badge fg-badge--neutral">
          {{ member.privacy_mode === 'handover' ? '移交本人' : '永久管理' }}
        </span>
      </div>

      <!-- 称谓（V2.3 KI-5）：resolve 结果 + 个人纠正；flag 关闭时自动隐藏 -->
      <section class="drawer-section">
        <h3 class="section-title">称谓关系</h3>
        <KinshipTermPanel :member-id="memberId" />
      </section>

      <!-- 查看态：基本信息分节卡 -->
      <section class="drawer-section">
        <h3 class="section-title">基本信息</h3>
        <NDescriptions v-if="!editing" :column="1" bordered data-test="profile-view">
          <NDescriptionsItem label="名字">{{ member.name }}</NDescriptionsItem>
          <NDescriptionsItem label="性别">{{ genderLabel(member.gender) }}</NDescriptionsItem>
          <NDescriptionsItem label="出生">{{ formatDate(member.birth) }}</NDescriptionsItem>
          <NDescriptionsItem label="去世">{{ formatDate(member.death) }}</NDescriptionsItem>
          <NDescriptionsItem label="简介">{{ member.bio || '—' }}</NDescriptionsItem>
        </NDescriptions>

        <!-- 编辑态（permissions.edit 才可进入） -->
        <NForm
          v-else
          label-placement="top"
          :show-feedback="false"
          data-test="profile-edit-form"
          @submit.prevent="saveEdit"
        >
          <NFormItem label="名字" required>
            <NInput v-model:value="editForm.name" :maxlength="100" data-test="edit-name" />
          </NFormItem>
          <NFormItem label="性别">
            <NRadioGroup v-model:value="editForm.gender" data-test="edit-gender">
              <NRadio value="f">女</NRadio>
              <NRadio value="m">男</NRadio>
              <NRadio value="unknown">不详</NRadio>
            </NRadioGroup>
          </NFormItem>
          <NFormItem label="出生">
            <div class="date-row">
              <NSelect
                v-model:value="editForm.birthCalType"
                class="cal-select"
                :options="calOptions"
                data-test="edit-birth-cal"
              />
              <NDatePicker
                v-if="editForm.birthCalType !== 'none'"
                :formatted-value="editForm.birthDate || null"
                type="date"
                value-format="yyyy-MM-dd"
                data-test="edit-birth-date"
                @update:formatted-value="(v: string | null) => (editForm.birthDate = v ?? '')"
              />
            </div>
          </NFormItem>
          <NFormItem label="简介">
            <NInput
              v-model:value="editForm.bio"
              type="textarea"
              :rows="2"
              :maxlength="2000"
              data-test="edit-bio"
            />
          </NFormItem>
          <p v-if="editError" class="error" data-test="edit-error">{{ editError }}</p>
          <div class="actions">
            <NButton data-test="edit-cancel" @click="editing = false">取消</NButton>
            <NButton type="primary" :loading="savingEdit" data-test="edit-save" @click="saveEdit">
              保存
            </NButton>
          </div>
        </NForm>
        <NButton
          v-if="member.permissions.edit && !editing"
          type="primary"
          secondary
          class="section-action"
          data-test="start-edit"
          @click="startEdit"
        >
          编辑档案
        </NButton>
      </section>

      <!-- 披露开关组（AD-9）：修改权 = 编辑权主体 -->
      <section v-if="member.permissions.edit" class="drawer-section" data-test="disclosure-group">
        <h3 class="section-title">家族空间外披露</h3>
        <p class="section-desc">
          对非同空间且无直系关系的族人，名字与称谓始终可见；以下内容按开关决定是否公开。
        </p>
        <div class="switch-row">
          <span>头像</span>
          <NSwitch v-model:value="disclosureDraft.avatar" data-test="disclosure-avatar" />
        </div>
        <div class="switch-row">
          <span>相册照片</span>
          <NSwitch v-model:value="disclosureDraft.photos" data-test="disclosure-photos" />
        </div>
        <div class="switch-row">
          <span>生卒日期</span>
          <NSwitch v-model:value="disclosureDraft.dates" data-test="disclosure-dates" />
        </div>
        <div class="switch-row">
          <span>简介</span>
          <NSwitch v-model:value="disclosureDraft.bio" data-test="disclosure-bio" />
        </div>
        <div class="switch-row">
          <span>链接附件</span>
          <NSwitch v-model:value="disclosureDraft.attachments" data-test="disclosure-attachments" />
        </div>
        <NButton
          type="primary"
          :loading="savingDisclosure"
          class="section-action"
          data-test="disclosure-save"
          @click="saveDisclosure"
        >
          保存披露设置
        </NButton>
      </section>

      <!-- 附件分节（权限由后端强制） -->
      <section class="drawer-section">
        <h3 class="section-title">附件</h3>
        <AttachmentsSection :user-id="memberId" :can-edit="member?.permissions?.edit === true" />
      </section>

      <!-- 危险区（permissions.delete 才显示） -->
      <section v-if="member.permissions.delete" class="drawer-section danger">
        <h3 class="section-title">删除档案</h3>
        <p class="section-desc">将同时移除其账号、会话与关联数据，且不可恢复。</p>
        <NButton type="error" secondary data-test="delete-btn" @click="askDelete">
          删除此档案
        </NButton>
      </section>

      <!-- 删除二次确认：输入名字 -->
      <NModal
        v-model:show="deleteDialogVisible"
        preset="card"
        title="确认删除档案"
        data-test="delete-confirm-dialog"
      >
        <p class="confirm-text">
          此操作不可恢复。请输入档案名字
          <strong>{{ member.name }}</strong> 以确认：
        </p>
        <NInput v-model:value="confirmName" placeholder="输入名字确认" :input-props="confirmNameInputProps" />
        <p v-if="deleteError" class="error" data-test="delete-error">{{ deleteError }}</p>
        <template #footer>
          <div class="modal-actions">
            <NButton data-test="delete-cancel" @click="deleteDialogVisible = false">取消</NButton>
            <NButton
              type="error"
              :disabled="confirmName !== member.name"
              :loading="deleting"
              data-test="delete-submit"
              @click="doDelete"
            >
              确认删除
            </NButton>
          </div>
        </template>
      </NModal>
    </template>
  </NDrawer>
</template>

<style scoped>
.badges {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

/* 领域状态徽章走 tokens.css 的 .fg-badge--* 工具类（design.md §3.4 全站统一） */

/* 分节卡：纸墨=纸面立牌分节；清雅=白底圆角分区（观感由 token 驱动） */
.drawer-section {
  margin-bottom: 16px;
  padding: 14px 16px 16px;
  background-color: var(--fg-surface);
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
}

.section-title {
  margin: 0 0 8px;
  font-family: var(--fg-font-display);
  font-size: 14px;
  font-weight: 700;
  color: var(--fg-ink);
}

.section-desc {
  margin: 0 0 10px;
  color: var(--fg-ink-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.section-action {
  margin-top: 12px;
}

.switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 14px;
  color: var(--fg-ink);
}

.date-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.cal-select {
  width: 110px;
  flex-shrink: 0;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.danger {
  border-color: color-mix(in srgb, var(--fg-status-disputed) 35%, transparent);
}

.confirm-text {
  margin: 0 0 12px;
  line-height: 1.6;
}

.error {
  margin: 8px 0 0;
  color: var(--fg-status-disputed);
  font-size: 13px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>

<style>
/* n-drawer / n-modal 根节点 teleport 到 body，scoped 选择器不可达：
   用 data-test 锚定删除确认弹窗宽度（内容样式仍走 scoped，slot 内容带 scope id） */
[data-test='delete-confirm-dialog'] {
  width: min(380px, calc(100vw - 48px));
}
</style>
