<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { useMembersStore } from '@/stores/members'
import type { ClanDisclosure, GenderType, StructuredDate } from '@/types/api'

/**
 * 档案抽屉：查看 / 按权编辑 / 披露开关组（AD-9）/ 删除输入名字确认。
 * 编辑与删除入口由后端返回的 permissions 控制，无权态直接隐藏操作。
 */
const props = defineProps<{ memberId: number }>()

const emit = defineEmits<{ close: [] }>()

const members = useMembersStore()

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
    ElMessage.success('档案已更新')
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
    ElMessage.success('披露设置已更新')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    savingDisclosure.value = false
  }
}

// ---- 删除确认流 ----

const deleteDialogVisible = ref(false)
const confirmName = ref('')
const deleting = ref(false)
const deleteError = ref('')

function askDelete(): void {
  confirmName.value = ''
  deleteError.value = ''
  deleteDialogVisible.value = true
}

async function doDelete(): Promise<void> {
  if (!member.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await members.remove(member.value.id, confirmName.value)
    ElMessage.success('档案已删除')
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
</script>

<template>
  <el-drawer
    :model-value="true"
    :title="member?.name ?? '档案'"
    size="420px"
    data-test="profile-drawer"
    @close="emit('close')"
  >
    <template v-if="member">
      <div class="badges" data-test="drawer-badges">
        <el-tag :type="member.claim_status === 'claimed' ? 'success' : 'warning'" size="small">
          {{ member.claim_status === 'claimed' ? '已认领' : '待认领' }}
        </el-tag>
        <el-tag type="info" size="small">
          {{ member.privacy_mode === 'handover' ? '移交本人' : '永久管理' }}
        </el-tag>
      </div>

      <!-- 查看态 -->
      <el-descriptions v-if="!editing" :column="1" border class="section" data-test="profile-view">
        <el-descriptions-item label="名字">{{ member.name }}</el-descriptions-item>
        <el-descriptions-item label="性别">{{ genderLabel(member.gender) }}</el-descriptions-item>
        <el-descriptions-item label="出生">{{ formatDate(member.birth) }}</el-descriptions-item>
        <el-descriptions-item label="去世">{{ formatDate(member.death) }}</el-descriptions-item>
        <el-descriptions-item label="简介">{{ member.bio || '—' }}</el-descriptions-item>
      </el-descriptions>

      <!-- 编辑态（permissions.edit 才可进入） -->
      <el-form
        v-else
        label-position="top"
        class="section"
        data-test="profile-edit-form"
        @submit.prevent="saveEdit"
      >
        <el-form-item label="名字" required>
          <el-input v-model="editForm.name" maxlength="100" data-test="edit-name" />
        </el-form-item>
        <el-form-item label="性别">
          <el-radio-group v-model="editForm.gender" data-test="edit-gender">
            <el-radio value="f">女</el-radio>
            <el-radio value="m">男</el-radio>
            <el-radio value="unknown">不详</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="出生">
          <div class="date-row">
            <el-select v-model="editForm.birthCalType" class="cal-select" data-test="edit-birth-cal">
              <el-option label="公历" value="solar" />
              <el-option label="农历" value="lunar" />
              <el-option label="不详" value="none" />
            </el-select>
            <el-date-picker
              v-if="editForm.birthCalType !== 'none'"
              v-model="editForm.birthDate"
              type="date"
              value-format="YYYY-MM-DD"
              data-test="edit-birth-date"
            />
          </div>
        </el-form-item>
        <el-form-item label="简介">
          <el-input v-model="editForm.bio" type="textarea" :rows="2" maxlength="2000" data-test="edit-bio" />
        </el-form-item>
        <p v-if="editError" class="error" data-test="edit-error">{{ editError }}</p>
        <div class="actions">
          <el-button data-test="edit-cancel" @click="editing = false">取消</el-button>
          <el-button type="primary" :loading="savingEdit" data-test="edit-save" @click="saveEdit">
            保存
          </el-button>
        </div>
      </el-form>

      <el-button
        v-if="member.permissions.edit && !editing"
        type="primary"
        plain
        class="section"
        data-test="start-edit"
        @click="startEdit"
      >
        编辑档案
      </el-button>

      <!-- 披露开关组（AD-9）：修改权 = 编辑权主体 -->
      <section v-if="member.permissions.edit" class="section disclosure" data-test="disclosure-group">
        <h3 class="block-title">家族空间外披露</h3>
        <p class="block-desc">对非同空间且无直系关系的族人，名字与称谓始终可见；以下内容按开关决定是否公开。</p>
        <div class="switch-row"><span>头像</span><el-switch v-model="disclosureDraft.avatar" data-test="disclosure-avatar" /></div>
        <div class="switch-row"><span>相册照片</span><el-switch v-model="disclosureDraft.photos" data-test="disclosure-photos" /></div>
        <div class="switch-row"><span>生卒日期</span><el-switch v-model="disclosureDraft.dates" data-test="disclosure-dates" /></div>
        <div class="switch-row"><span>简介</span><el-switch v-model="disclosureDraft.bio" data-test="disclosure-bio" /></div>
        <div class="switch-row"><span>链接附件</span><el-switch v-model="disclosureDraft.attachments" data-test="disclosure-attachments" /></div>
        <el-button
          type="primary"
          :loading="savingDisclosure"
          data-test="disclosure-save"
          @click="saveDisclosure"
        >
          保存披露设置
        </el-button>
      </section>

      <!-- 危险区（permissions.delete 才显示） -->
      <section v-if="member.permissions.delete" class="danger">
        <h3 class="block-title">删除档案</h3>
        <p class="block-desc">将同时移除其账号、会话与关联数据，且不可恢复。</p>
        <el-button type="danger" plain data-test="delete-btn" @click="askDelete">删除此档案</el-button>
      </section>

      <!-- 删除二次确认：输入名字 -->
      <el-dialog
        v-model="deleteDialogVisible"
        title="确认删除档案"
        width="380px"
        append-to-body
        data-test="delete-confirm-dialog"
      >
        <p class="confirm-text">
          此操作不可恢复。请输入档案名字
          <strong>{{ member.name }}</strong> 以确认：
        </p>
        <el-input v-model="confirmName" placeholder="输入名字确认" data-test="delete-confirm-input" />
        <p v-if="deleteError" class="error" data-test="delete-error">{{ deleteError }}</p>
        <template #footer>
          <el-button data-test="delete-cancel" @click="deleteDialogVisible = false">取消</el-button>
          <el-button
            type="danger"
            :disabled="confirmName !== member.name"
            :loading="deleting"
            data-test="delete-submit"
            @click="doDelete"
          >
            确认删除
          </el-button>
        </template>
      </el-dialog>
    </template>
  <AttachmentsSection :user-id="memberId" :can-edit="member?.permissions?.edit === true" />
  </el-drawer>
</template>

<style scoped>
.badges {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.section {
  margin-bottom: 20px;
}

.block-title {
  margin: 0 0 6px;
  font-size: 14px;
}

.block-desc {
  margin: 0 0 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.switch-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  font-size: 14px;
}

.disclosure .el-button {
  margin-top: 10px;
}

.date-row {
  display: flex;
  gap: 8px;
  width: 100%;
}

.cal-select {
  width: 110px;
}

.actions {
  display: flex;
  gap: 8px;
}

.danger {
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 16px;
}

.confirm-text {
  margin: 0 0 12px;
  line-height: 1.6;
}

.error {
  color: var(--el-color-danger);
  font-size: 13px;
}
</style>
