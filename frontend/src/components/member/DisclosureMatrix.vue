<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { ApiError } from '@/api/errors'
import { fetchDisclosureMatrix } from '@/api/members'
import { useAuthStore } from '@/stores/auth'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import {
  DISCLOSURE_CATEGORIES,
  DISCLOSURE_CATEGORY_LABELS,
  HIGH_RISK_DISCLOSURE_CATEGORIES,
  type DisclosureCategory,
  type DisclosureMatrix,
} from '@/types/api'

/**
 * 披露偏好矩阵（v2 §0.1 / Gap3）：类别 ×（全局 / 每空间）。
 * - 基础五类：全局与逐空间覆盖均可自助开关（逐空间仅档案本人可改，后端强制）；
 * - 高敏感类别（health/address/school/contact/private_notes）：恒禁用 —— 合同上
 *   只能为 false，任何层级不得自动开放；
 * - 默认全关；全局 false 由缺省表达，逐空间行显式落盘以支持双向覆盖。
 */
const auth = useAuthStore()
const members = useMembersStore()
const spaces = useSpacesStore()

const saving = ref(false)

function emptyFlags(): Record<DisclosureCategory, boolean> {
  return Object.fromEntries(DISCLOSURE_CATEGORIES.map((c) => [c, false])) as Record<
    DisclosureCategory,
    boolean
  >
}

/** 本地草稿：保存成功前不动服务端状态 */
const draftGlobal = reactive(emptyFlags())
/** 每空间覆盖草稿（space_id → 类别布尔） */
const draftBySpace = reactive<Record<number, Record<DisclosureCategory, boolean>>>({})
/** 服务端已保存的每空间值（用于差异保存） */
const savedBySpace = new Map<number, Record<DisclosureCategory, boolean>>()

onMounted(async () => {
  if (members.members.length === 0) {
    await members.load().catch(() => undefined)
  }
  spaces.load().catch(() => undefined)
  await loadMatrix().catch(() => syncFromSelfFallback())
})

async function loadMatrix(): Promise<void> {
  const self = auth.user
  if (!self) return
  const matrix = await fetchDisclosureMatrix(self.id)
  applyMatrix(matrix)
}

function applyMatrix(matrix: DisclosureMatrix): void {
  Object.assign(draftGlobal, emptyFlags(), matrix.global)
  for (const entry of matrix.spaces) {
    const flags = { ...emptyFlags(), ...entry.allowed }
    savedBySpace.set(entry.space_id, flags)
    draftBySpace[entry.space_id] = { ...flags }
  }
}

/** 矩阵不可用时兑底：以 Member.clan_disclosure（全局基础五类）同步 */
function syncFromSelfFallback(): void {
  const self = members.members.find((m) => m.id === auth.user?.id)
  if (!self) return
  for (const category of DISCLOSURE_CATEGORIES) {
    draftGlobal[category] =
      (self.clan_disclosure as Partial<Record<DisclosureCategory, boolean>>)[category] ?? false
  }
}

function isHighRisk(category: DisclosureCategory): boolean {
  return HIGH_RISK_DISCLOSURE_CATEGORIES.includes(category)
}

function spaceDraft(spaceId: number): Record<DisclosureCategory, boolean> {
  if (!draftBySpace[spaceId]) draftBySpace[spaceId] = emptyFlags()
  return draftBySpace[spaceId]
}

function spaceChanged(spaceId: number): boolean {
  const baseline = savedBySpace.get(spaceId)
  if (!baseline) {
    // 尚无已保存覆盖：任一 true 即为变更
    return DISCLOSURE_CATEGORIES.some((c) => !isHighRisk(c) && spaceDraft(spaceId)[c])
  }
  return DISCLOSURE_CATEGORIES.some((c) => spaceDraft(spaceId)[c] !== baseline[c])
}

async function save(): Promise<void> {
  const selfId = auth.user?.id
  if (!selfId) return
  saving.value = true
  try {
    await members.setDisclosure(selfId, fiveFlags(draftGlobal))
    for (const space of spaces.spaces) {
      if (spaceChanged(space.id)) {
        await members.setDisclosure(selfId, fiveFlags(spaceDraft(space.id)), space.id)
      }
    }
    ElMessage.success('披露偏好已更新')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

function fiveFlags(source: Record<DisclosureCategory, boolean>) {
  return {
    avatar: source.avatar,
    photos: source.photos,
    dates: source.dates,
    bio: source.bio,
    attachments: source.attachments,
  }
}
</script>

<template>
  <section class="disclosure-matrix" data-test="disclosure-matrix">
    <p class="desc">
      对非同空间且无直系关系的族人，名字与称谓始终可见；以下内容按开关决定是否公开。默认全部不公开；
      高敏感类别与健康、住址等信息不因任何身份自动开放。未成年人档案始终按最小披露遮蔽。
    </p>
    <el-table :data="[...DISCLOSURE_CATEGORIES]" size="small" data-test="disclosure-table">
      <el-table-column label="类别" width="120">
        <template #default="{ row }">{{ DISCLOSURE_CATEGORY_LABELS[row as DisclosureCategory] }}</template>
      </el-table-column>
      <el-table-column label="全局" width="100">
        <template #default="{ row }">
          <el-tooltip
            v-if="isHighRisk(row as DisclosureCategory)"
            content="高敏感类别：合同恒不公开，任何层级不得自动开放"
            placement="top"
          >
            <el-switch v-model="draftGlobal[row as DisclosureCategory]" disabled data-test="disclosure-switch-disabled" />
          </el-tooltip>
          <el-switch
            v-else
            v-model="draftGlobal[row as DisclosureCategory]"
            :data-test="`disclosure-switch-${row}`"
          />
        </template>
      </el-table-column>
      <el-table-column
        v-for="space in spaces.spaces"
        :key="space.id"
        :label="space.name"
        width="140"
      >
        <template #default="{ row }">
          <el-tooltip
            v-if="isHighRisk(row as DisclosureCategory)"
            content="高敏感类别：合同恒不公开，任何层级不得自动开放"
            placement="top"
          >
            <el-switch :model-value="false" disabled :data-test="`disclosure-space-disabled-${space.id}`" />
          </el-tooltip>
          <el-switch
            v-else
            v-model="spaceDraft(space.id)[row as DisclosureCategory]"
            :data-test="`disclosure-space-${space.id}-${row}`"
          />
        </template>
      </el-table-column>
    </el-table>
    <div class="actions">
      <span class="hint">基础五类可按空间覆盖全局偏好{{ spaces.spaces.length ? '；高敏感类别恒不公开' : '' }}。</span>
      <el-button type="primary" :loading="saving" data-test="disclosure-save" @click="save">
        保存披露偏好
      </el-button>
    </div>
  </section>
</template>

<style scoped>
.desc {
  margin: 0 0 10px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
