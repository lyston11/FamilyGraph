<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NDataTable, NModal, NSwitch, NTooltip, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'

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
 * 披露偏好矩阵（v2 §0.1 / Gap3；09-05 高敏感策略放开）：
 * - 基础五类：全局与逐空间覆盖均可自助开关（逐空间仅档案本人可改，后端强制）；
 * - 高敏感类别（health/address/school/contact/private_notes）：默认关闭，本人可
 *   开启且必须经强确认 Modal（后果知情）；未成年人本人矩阵中开关禁用（后端
 *   对高敏感开启请求整体 422，双保险）；
 * - 保存为整体替换语义：基础五类必填 + 高敏感全量随草稿提交。
 */
const auth = useAuthStore()
const members = useMembersStore()
const spaces = useSpacesStore()
const message = useMessage()

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

/** 本人是否未成年人（由结构化出生日期推导；与后端 is_minor 同语义，无法解析按成年） */
const isMinorSelf = computed<boolean>(() => {
  const self = members.members.find((m) => m.id === auth.user?.id)
  const date = self?.birth?.date
  if (!date) return false
  const year = Number(date.slice(0, 4))
  if (!Number.isFinite(year)) return false
  const now = new Date()
  let age = now.getFullYear() - year
  const month = Number(date.slice(5, 7))
  const day = Number(date.slice(8, 10))
  if (
    now.getMonth() + 1 < month ||
    (now.getMonth() + 1 === month && now.getDate() < (Number.isFinite(day) ? day : 1))
  ) {
    age -= 1
  }
  return age < 18
})

const MINOR_HINT = '未成年人档案始终按最小披露遮蔽，不可开启高敏感类别'

/** 高敏感强确认：记录待应用动作，确认后落草稿（关闭操作不需要确认） */
const confirmState = reactive({
  show: false,
  categoryLabel: '',
  apply: null as (() => void) | null,
})

function requestHighRiskEnable(apply: () => void, categoryLabel: string): void {
  confirmState.apply = apply
  confirmState.categoryLabel = categoryLabel
  confirmState.show = true
}

function confirmHighRiskEnable(): void {
  confirmState.apply?.()
  confirmState.show = false
}

function spaceDraft(spaceId: number): Record<DisclosureCategory, boolean> {
  if (!draftBySpace[spaceId]) draftBySpace[spaceId] = emptyFlags()
  return draftBySpace[spaceId]
}

function spaceChanged(spaceId: number): boolean {
  const baseline = savedBySpace.get(spaceId)
  if (!baseline) {
    // 尚无已保存覆盖：任一 true 即为变更（含高敏感——09-05 起可开放）
    return DISCLOSURE_CATEGORIES.some((c) => spaceDraft(spaceId)[c])
  }
  return DISCLOSURE_CATEGORIES.some((c) => spaceDraft(spaceId)[c] !== baseline[c])
}

async function save(): Promise<void> {
  const selfId = auth.user?.id
  if (!selfId) return
  saving.value = true
  try {
    await members.setDisclosure(selfId, { ...draftGlobal })
    for (const space of spaces.spaces) {
      if (spaceChanged(space.id)) {
        await members.setDisclosure(selfId, { ...spaceDraft(space.id) }, space.id)
      }
    }
    message.success('披露偏好已更新')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '保存失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

/** 单元格开关：基础类直连草稿；高敏感=开启需强确认（未成年人禁用） */
function renderSwitch(
  category: DisclosureCategory,
  getValue: () => boolean,
  setValue: (value: boolean) => void,
  dataTest: string,
): ReturnType<typeof h> {
  if (isHighRisk(category) && isMinorSelf.value) {
    return h(
      NTooltip,
      { trigger: 'hover', placement: 'top' },
      {
        trigger: () =>
          h(NSwitch, {
            value: false,
            disabled: true,
            'data-test': `${dataTest}-minor-locked`,
          }),
        default: () => MINOR_HINT,
      },
    )
  }
  return h(NSwitch, {
    value: getValue(),
    'onUpdate:value': (value: boolean) => {
      if (isHighRisk(category) && value) {
        // 开启高敏感：强确认后才落草稿（关闭不弹）
        requestHighRiskEnable(() => setValue(true), DISCLOSURE_CATEGORY_LABELS[category])
        return
      }
      setValue(value)
    },
    'data-test': dataTest,
  })
}

/** 矩阵行：类别键（n-data-table 行必须是对象） */
interface CategoryRow {
  category: DisclosureCategory
}

const categoryRows: CategoryRow[] = DISCLOSURE_CATEGORIES.map((category) => ({ category }))

const columns = computed<DataTableColumns<CategoryRow>>(() => {
  const base: DataTableColumns<CategoryRow> = [
    {
      title: '类别',
      key: 'category',
      width: 110,
      render: (row) => DISCLOSURE_CATEGORY_LABELS[row.category],
    },
    {
      title: '全局',
      key: 'global',
      width: 84,
      render: (row) =>
        renderSwitch(
          row.category,
          () => draftGlobal[row.category],
          (value) => {
            draftGlobal[row.category] = value
          },
          `disclosure-switch-${row.category}`,
        ),
    },
  ]
  for (const space of spaces.spaces) {
    base.push({
      title: space.name,
      key: `space-${space.id}`,
      width: 120,
      render: (row) =>
        renderSwitch(
          row.category,
          () => spaceDraft(space.id)[row.category],
          (value) => {
            spaceDraft(space.id)[row.category] = value
          },
          `disclosure-space-${space.id}-${row.category}`,
        ),
    })
  }
  return base
})

/** 表格总宽下限（类别 110 + 全局 84 + 逐空间 120/列）：
 *  375px 下表格区域内部横向滚动，页面本身不横向滚动（Phase 7 响应式） */
const tableScrollX = computed(
  () => 194 + 120 * Math.max(1, spaces.spaces.length),
)
</script>

<template>
  <section class="disclosure-matrix" data-test="disclosure-matrix">
    <p class="desc">
      对非同空间且无直系关系的族人，名字与称谓始终可见；以下内容按开关决定是否公开。默认全部不公开；
      高敏感类别默认关闭，本人可开启（需二次确认）；未成年人档案始终按最小披露遮蔽。
    </p>
    <NDataTable
      size="small"
      :scroll-x="tableScrollX"
      :columns="columns"
      :data="categoryRows"
      :row-key="(row: CategoryRow) => row.category"
      data-test="disclosure-table"
    />
    <div class="actions">
      <span class="hint">基础五类可按空间覆盖全局偏好；高敏感类别开启需二次确认，可随时关闭。</span>
      <NButton type="primary" :loading="saving" data-test="disclosure-save" @click="save">
        保存披露偏好
      </NButton>
    </div>

    <!-- 高敏感开启强确认（PRD R5）：teleport 到 body，断言走 document 查询 -->
    <NModal
      v-model:show="confirmState.show"
      preset="dialog"
      type="warning"
      title="开启高敏感信息披露？"
      positive-text="确认开启"
      negative-text="取消"
      data-test="disclosure-high-risk-confirm"
      @positive-click="confirmHighRiskEnable"
    >
      <p>
        确认开启「{{ confirmState.categoryLabel }}」？开启后，对应可见范围内的家庭成员将能看到
        这类信息；你可随时回到本页关闭。
      </p>
      <p>未成年人档案始终按最小披露遮蔽，不受此设置影响。</p>
    </NModal>
  </section>
</template>

<style scoped>
.desc {
  margin: 0 0 10px;
  color: var(--fg-ink-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
  gap: 12px;
}

.hint {
  color: var(--fg-ink-secondary);
  font-size: 12px;
}
</style>
