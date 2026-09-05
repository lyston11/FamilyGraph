<script setup lang="ts">
/**
 * 设置页「邀请码」区块（09-05 决策 11/13/14）：
 * 1) 我的码列表（kind 徽标 / 使用次数 / 有效期 / 状态）+ 撤销 + 复制分享链接；
 * 2) 创建三类码（家庭/家族码须选择所在空间；陌生人码可设次数上限）；
 * 3) 填码加入（household/lineage；后端 409 已是成员 / 400 陌生人码等文案原样呈现）；
 * 4) 我的待确认绑定（GET /bindings → 确认需输 PIN → confirm/reject，决策 16）。
 *
 * 权限态：建码资格由服务端裁定（provisional 403）；前端对 provisional 用户
 * 隐藏建码表单并提示（auth.user.profile_status 判定，与守卫同源）。
 * 所有错误文案原样呈现后端 message（error-handling.md），不做二次改写。
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  NButton,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  useMessage,
} from 'naive-ui'

import { ApiError } from '@/api/errors'
import { fetchSpaces } from '@/api/spaces'
import {
  createInviteCode,
  fetchMyInviteCodes,
  redeemInviteCode,
  revokeInviteCode,
} from '@/api/inviteCodes'
import { confirmBinding, fetchMyBindings, rejectBinding } from '@/api/bindings'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { Binding, FamilySpace, InviteCode, InviteCodeKindFull } from '@/types/api'

const auth = useAuthStore()
const spacesStore = useSpacesStore()
const message = useMessage()

// ---- 建码资格：与路由守卫同源（provisional → identity_confirmed） ----
const canCreateCodes = computed(() => auth.user?.profile_status === 'identity_confirmed')

// ---- 我的码列表 ----
const codes = ref<InviteCode[] | null>(null)

const KIND_LABELS: Record<InviteCodeKindFull, string> = {
  household: '家庭码',
  lineage: '家族码',
  stranger: '陌生人码',
}

type CodeStatus = '有效' | '已撤销' | '已过期' | '已用尽'

function codeStatus(code: InviteCode, now: Date = new Date()): CodeStatus {
  if (code.revoked_at !== null) return '已撤销'
  if (new Date(code.expires_at).getTime() <= now.getTime()) return '已过期'
  if (code.max_uses !== null && code.used_count >= code.max_uses) return '已用尽'
  return '有效'
}

function usesText(code: InviteCode): string {
  if (code.kind === 'stranger') {
    return code.max_uses === null ? `已用 ${code.used_count} 次` : `${code.used_count}/${code.max_uses}`
  }
  return `${code.used_count}/1`
}

/** 日期投影（ISO 字符串 → YYYY-MM-DD）；后端时间即本地服务时间，无时区换算面 */
function formatDate(iso: string): string {
  return iso.slice(0, 10)
}

/** 分享链接：注册页自动回填端（决策 12） */
function shareLink(code: InviteCode): string {
  return `${window.location.origin}/register?code=${code.code}`
}

async function copyShareLink(code: InviteCode): Promise<void> {
  const link = shareLink(code)
  try {
    await navigator.clipboard.writeText(link)
    message.success('分享链接已复制')
  } catch {
    // jsdom/非安全上下文无 clipboard API：回退隐藏 textarea + execCommand
    const textarea = document.createElement('textarea')
    textarea.value = link
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    message.success('分享链接已复制')
  }
}

async function loadCodes(): Promise<void> {
  codes.value = await fetchMyInviteCodes()
}

async function revoke(code: InviteCode): Promise<void> {
  try {
    const revoked = await revokeInviteCode(code.id)
    codes.value = (codes.value ?? []).map((row) => (row.id === revoked.id ? revoked : row))
    message.success('邀请码已撤销')
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '撤销失败，请稍后重试')
  }
}

// ---- 创建三类码 ----
const createForm = ref<{ kind: InviteCodeKindFull; spaceId: number | null; maxUses: number | null; ttlDays: number }>(
  { kind: 'household', spaceId: null, maxUses: null, ttlDays: 7 },
)
const creating = ref(false)
const createError = ref('')

const mySpaces = ref<FamilySpace[]>([])
const spaceOptions = computed(() =>
  mySpaces.value
    .filter((space) => space.kind === createForm.value.kind)
    .map((space) => ({ label: space.name, value: space.id })),
)

const ttlOptions = [7, 30, 90, 365].map((days) => ({ label: `${days} 天`, value: days }))

// 切换类型时重置空间选择：household 与 lineage 的可选空间集合不同，
// 保留旧值会提交错误 kind 的 space_id（后端 404）。
watch(
  () => createForm.value.kind,
  () => {
    createForm.value.spaceId = null
  },
)

async function submitCreate(): Promise<void> {
  createError.value = ''
  if (createForm.value.kind !== 'stranger' && createForm.value.spaceId === null) {
    createError.value = '请选择邀请码所属的空间'
    return
  }
  creating.value = true
  try {
    const created = await createInviteCode({
      kind: createForm.value.kind,
      space_id: createForm.value.kind === 'stranger' ? null : createForm.value.spaceId,
      max_uses: createForm.value.kind === 'stranger' ? createForm.value.maxUses : null,
      ttl_days: createForm.value.ttlDays,
    })
    codes.value = [created, ...(codes.value ?? [])]
    message.success(`邀请码 ${created.code} 已创建`)
  } catch (error) {
    createError.value = error instanceof ApiError ? error.message : '创建失败，请稍后重试'
  } finally {
    creating.value = false
  }
}

// ---- 填码加入 ----
const redeemCode = ref('')
const redeeming = ref(false)
const redeemError = ref('')

async function submitRedeem(): Promise<void> {
  redeemError.value = ''
  if (!redeemCode.value.trim()) {
    redeemError.value = '请输入邀请码'
    return
  }
  redeeming.value = true
  try {
    const redeemed = await redeemInviteCode(redeemCode.value.trim().toUpperCase())
    message.success(`已加入空间「${redeemed.space_name ?? ''}」`)
    redeemCode.value = ''
    // 加入新空间后刷新空间缓存（state-management.md：成员资格变更强制刷新）
    void spacesStore.load()
  } catch (error) {
    redeemError.value = error instanceof ApiError ? error.message : '兑换失败，请稍后重试'
  } finally {
    redeeming.value = false
  }
}

// ---- 我的待确认绑定（决策 16） ----
const bindings = ref<Binding[]>([])

const pendingBindings = computed(() => bindings.value.filter((row) => row.status === 'pending'))

async function loadBindings(): Promise<void> {
  bindings.value = await fetchMyBindings()
}

// 确认弹窗：PIN 复验（后端复用登录侧校验惯例）
const confirmTarget = ref<Binding | null>(null)
const confirmPin = ref('')
const confirming = ref(false)
const confirmError = ref('')

function openConfirm(binding: Binding): void {
  confirmTarget.value = binding
  confirmPin.value = ''
  confirmError.value = ''
}

async function submitConfirm(): Promise<void> {
  if (confirmTarget.value === null) return
  if (!/^\d{6}$/.test(confirmPin.value)) {
    confirmError.value = '请输入 6 位数字 PIN 码'
    return
  }
  confirming.value = true
  try {
    await confirmBinding(confirmTarget.value.id, confirmPin.value)
    message.success('已确认这是我，档案已合并')
    confirmTarget.value = null
    await loadBindings()
  } catch (error) {
    confirmError.value = error instanceof ApiError ? error.message : '确认失败，请稍后重试'
  } finally {
    confirming.value = false
  }
}

async function rejectOne(binding: Binding): Promise<void> {
  try {
    await rejectBinding(binding.id)
    message.success('已拒绝该绑定请求')
    await loadBindings()
  } catch (error) {
    message.error(error instanceof ApiError ? error.message : '操作失败，请稍后重试')
  }
}

const STATUS_LABELS: Record<Binding['status'], string> = {
  pending: '待确认',
  confirmed: '已确认',
  rejected: '已拒绝',
  cancelled: '已取消',
}

onMounted(() => {
  void loadCodes().catch(() => {
    codes.value = []
  })
  void loadBindings().catch(() => {
    bindings.value = []
  })
  // 空间选择器数据：仅建码表单需要；provisional 用户跳过
  if (canCreateCodes.value) {
    void fetchSpaces()
      .then((list) => {
        mySpaces.value = list
      })
      .catch(() => {
        mySpaces.value = []
      })
  }
})
</script>

<template>
  <div class="invite-code-section">
    <!-- 我的码列表 -->
    <div class="block" data-test="invite-codes-block">
      <h3 class="block-title">我的邀请码</h3>
      <p v-if="codes === null" class="empty" data-test="invite-codes-loading">加载中…</p>
      <p v-else-if="codes.length === 0" class="empty" data-test="invite-codes-empty">
        还没有创建过邀请码
      </p>
      <table v-else class="code-table" data-test="invite-codes-table">
        <thead>
          <tr>
            <th scope="col">邀请码</th>
            <th scope="col">类型</th>
            <th scope="col">使用次数</th>
            <th scope="col">有效期至</th>
            <th scope="col">状态</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="code in codes" :key="code.id" :data-test="`invite-code-row-${code.id}`">
            <td class="code-value">{{ code.code }}</td>
            <td>
              <span class="kind-badge" :class="`kind-${code.kind}`" :data-test="`invite-code-kind-${code.id}`">
                {{ KIND_LABELS[code.kind] }}
              </span>
              <span v-if="code.space_name" class="space-name">{{ code.space_name }}</span>
            </td>
            <td>{{ usesText(code) }}</td>
            <td>{{ formatDate(code.expires_at) }}</td>
            <td :data-test="`invite-code-status-${code.id}`">{{ codeStatus(code) }}</td>
            <td class="row-actions">
              <NButton
                text
                type="primary"
                :data-test="`invite-code-copy-${code.id}`"
                @click="copyShareLink(code)"
              >
                复制链接
              </NButton>
              <NButton
                v-if="code.revoked_at === null"
                text
                type="error"
                :data-test="`invite-code-revoke-${code.id}`"
                @click="revoke(code)"
              >
                撤销
              </NButton>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 建码入口：provisional 隐藏并提示（后端同样 403 兜底） -->
    <div class="block" data-test="invite-create-block">
      <h3 class="block-title">创建邀请码</h3>
      <p v-if="!canCreateCodes" class="empty" data-test="invite-create-disabled">
        完成身份确认后才能创建邀请码
      </p>
      <template v-else>
        <NForm inline :show-feedback="false" @submit.prevent="submitCreate">
          <NFormItem label="类型">
            <NRadioGroup v-model:value="createForm.kind" data-test="invite-create-kind">
              <NRadio value="household">家庭码</NRadio>
              <NRadio value="lineage">家族码</NRadio>
              <NRadio value="stranger">陌生人码</NRadio>
            </NRadioGroup>
          </NFormItem>
          <NFormItem v-if="createForm.kind !== 'stranger'" label="所在空间">
            <NSelect
              v-model:value="createForm.spaceId"
              :options="spaceOptions"
              placeholder="选择空间"
              class="space-select"
              data-test="invite-create-space"
            />
          </NFormItem>
          <NFormItem v-if="createForm.kind === 'stranger'" label="使用上限">
            <NInputNumber
              v-model:value="createForm.maxUses"
              :min="1"
              placeholder="不限"
              class="uses-input"
              data-test="invite-create-max-uses"
            />
          </NFormItem>
          <NFormItem label="有效期">
            <NSelect
              v-model:value="createForm.ttlDays"
              :options="ttlOptions"
              class="ttl-select"
              data-test="invite-create-ttl"
            />
          </NFormItem>
          <NFormItem>
            <NButton
              type="primary"
              :loading="creating"
              data-test="invite-create-submit"
              @click="submitCreate"
            >
              创建
            </NButton>
          </NFormItem>
        </NForm>
        <p v-if="createError" class="error" data-test="invite-create-error">{{ createError }}</p>
        <p class="meta-hint">家庭/家族码一次性有效；陌生人码供新家庭注册时归因使用。</p>
      </template>
    </div>

    <!-- 填码加入（家庭/家族码） -->
    <div class="block" data-test="invite-redeem-block">
      <h3 class="block-title">凭邀请码加入空间</h3>
      <NForm inline :show-feedback="false" @submit.prevent="submitRedeem">
        <NFormItem label="邀请码" :label-props="{ for: 'invite-redeem-code' }">
          <NInput
            v-model:value="redeemCode"
            :maxlength="10"
            placeholder="8-10 位邀请码"
            :input-props="{ id: 'invite-redeem-code' }"
            data-test="invite-redeem-input"
          />
        </NFormItem>
        <NFormItem>
          <NButton type="primary" :loading="redeeming" data-test="invite-redeem-submit" @click="submitRedeem">
            加入
          </NButton>
        </NFormItem>
      </NForm>
      <p v-if="redeemError" class="error" data-test="invite-redeem-error">{{ redeemError }}</p>
    </div>

    <!-- 我的待确认绑定 -->
    <div class="block" data-test="bindings-block">
      <h3 class="block-title">待确认的绑定请求</h3>
      <p v-if="pendingBindings.length === 0" class="empty" data-test="bindings-empty">
        暂无待确认的绑定请求
      </p>
      <ul v-else class="binding-list" data-test="bindings-list">
        <li v-for="binding in pendingBindings" :key="binding.id" class="binding-item" :data-test="`binding-item-${binding.id}`">
          <span class="binding-text">
            {{ binding.initiator_name ?? '有家人' }}为你建档了名为「{{ binding.person_name ?? '未知' }}」的人物，如果这是你，请确认。
          </span>
          <span class="row-actions">
            <NButton text type="primary" :data-test="`binding-confirm-${binding.id}`" @click="openConfirm(binding)">
              这是我
            </NButton>
            <NButton text type="error" :data-test="`binding-reject-${binding.id}`" @click="rejectOne(binding)">
              拒绝
            </NButton>
          </span>
        </li>
      </ul>
      <!-- 已决议历史（只读投影） -->
      <ul v-if="bindings.some((row) => row.status !== 'pending')" class="binding-history" data-test="bindings-history">
        <li v-for="binding in bindings.filter((row) => row.status !== 'pending')" :key="binding.id">
          「{{ binding.person_name ?? '未知' }}」的绑定请求：{{ STATUS_LABELS[binding.status] }}
        </li>
      </ul>
    </div>

    <!-- 确认绑定弹窗：PIN 复验 -->
    <NModal
      :show="confirmTarget !== null"
      preset="card"
      title="确认这是你本人"
      data-test="binding-confirm-dialog"
      @update:show="(show: boolean) => { if (!show) confirmTarget = null }"
    >
      <p class="meta-hint">
        确认后该人物将并入你的账号，并完成身份确认。请输入你的 PIN 码验证身份。
      </p>
      <NInput
        v-model:value="confirmPin"
        type="password"
        :maxlength="6"
        placeholder="6 位数字 PIN 码"
        :input-props="{ inputmode: 'numeric' }"
        data-test="binding-confirm-pin"
      />
      <p v-if="confirmError" class="error" data-test="binding-confirm-error">{{ confirmError }}</p>
      <template #footer>
        <div class="dialog-actions">
          <NButton data-test="binding-confirm-cancel" @click="confirmTarget = null">取消</NButton>
          <NButton type="primary" :loading="confirming" data-test="binding-confirm-submit" @click="submitConfirm">
            确认
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.invite-code-section {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.block-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--fg-ink);
}

.empty {
  margin: 0;
  font-size: 13px;
  color: var(--fg-ink-faint);
}

.meta-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.code-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.code-table th,
.code-table td {
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid var(--fg-line);
  color: var(--fg-ink);
}

.code-table th {
  font-weight: 600;
  color: var(--fg-ink-secondary);
}

.code-value {
  font-family: var(--fg-font-body);
  letter-spacing: 0.08em;
  font-weight: 600;
}

.kind-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--fg-radius-control);
  font-size: 12px;
  background-color: var(--fg-accent-soft);
  color: var(--fg-accent);
}

.kind-badge.kind-stranger {
  background-color: color-mix(in srgb, var(--fg-status-proposed) 10%, transparent);
  color: var(--fg-status-proposed);
}

.space-name {
  margin-left: 6px;
  font-size: 12px;
  color: var(--fg-ink-secondary);
}

.row-actions {
  display: inline-flex;
  gap: 10px;
  align-items: center;
}

.space-select {
  min-width: 180px;
}

.uses-input {
  width: 140px;
}

.ttl-select {
  width: 120px;
}

.error {
  margin: 8px 0 0;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--fg-status-disputed);
  background-color: color-mix(in srgb, var(--fg-status-disputed) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--fg-status-disputed) 35%, transparent);
  border-radius: var(--fg-radius-control);
}

.binding-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.binding-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-control);
  background: var(--fg-surface-raised);
}

.binding-text {
  font-size: 13px;
  color: var(--fg-ink);
}

.binding-history {
  margin: 12px 0 0;
  padding: 0 0 0 2px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--fg-ink-faint);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

@media (max-width: 600px) {
  .invite-code-section :deep(.n-form--inline .n-form-item) {
    width: 100%;
    margin-right: 0;
  }

  .invite-code-section :deep(.n-form--inline .n-form-item-blank) {
    flex: 1;
  }
}
</style>

<style>
/* n-modal 卡片根节点 teleport 到 body，scoped 选择器不可达：用 data-test 锚定宽度 */
[data-test='binding-confirm-dialog'] {
  width: min(400px, calc(100vw - 48px));
}
</style>
