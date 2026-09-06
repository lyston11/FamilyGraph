<script setup lang="ts">
/**
 * Agent Provider 治理页（/admin-api/v1/agent/*，09-06 治理迁移）。
 *
 * 单页三区块（design §5.3，全部 ag-* 既有类 + 组件内 scoped 补充）：
 * 1. Provider 注册表：列表（名称/kind/api/模型/secret 状态/启用）+ 注册/编辑
 *    表单。编辑走 PATCH 语义：仅提交变更字段；secret 留空=不变、显式输入=轮换、
 *    勾选"清除"=清除。响应与错误呈现永无密钥明文/密文（has_secret 布尔除外）。
 * 2. 平台默认模型：assistant / steward 两行（Provider 下拉 + model 下拉联动
 *    allowed_models）；PUT 全量覆盖——清除即该维度回退"未设默认"。
 * 3. 空间设置排查：输入 space_id 只读查看两 agent 维度行级设置与平台默认状态
 *    （原始存储态，不代替 owner 选择）。
 *
 * 错误呈现：AdminApiError.serverMessage + detail（如 allowed_models 白名单
 * 载荷）结构化展示；所有写操作经后端 admin_access_audits 审计。
 */
import { computed, onMounted, ref } from 'vue'
import PageState from '@/components/PageState.vue'
import {
  createProvider,
  getPlatformDefaults,
  getSpaceProviderSettings,
  listProviders,
  putPlatformDefaults,
  updateProvider,
} from '@/api/agent-provider'
import { AdminApiError } from '@/api/client'
import type {
  AdminAgentProviderCreatePayload,
  AdminAgentProviderOut,
  AdminAgentProviderPatchPayload,
  AdminAgentPlatformDefaultsPayload,
  AdminSpaceProviderSettingsOut,
  AgentPlatformDefaultsOut,
  AgentProviderKind,
} from '@/types/api'

// ---- 列表与加载 ----

const providers = ref<AdminAgentProviderOut[]>([])
const platformDefaults = ref<AgentPlatformDefaultsOut | null>(null)
const listState = ref<'loading' | 'ready' | 'error'>('loading')
const listError = ref('')

async function load(): Promise<void> {
  listState.value = 'loading'
  listError.value = ''
  try {
    const [rows, defaults] = await Promise.all([listProviders(), getPlatformDefaults()])
    providers.value = rows
    platformDefaults.value = defaults
    syncPdForm()
    listState.value = 'ready'
  } catch (error) {
    listError.value = describeError(error)
    listState.value = 'error'
  }
}

/** 错误文案：服务端消息优先，附结构化 detail（如 allowed_models 白名单）。 */
function describeError(error: unknown): string {
  if (error instanceof AdminApiError) {
    const detailText = formatDetail(error.detail)
    return detailText ? `${error.serverMessage}（${detailText}）` : error.serverMessage
  }
  return '操作失败，请稍后重试'
}

function formatDetail(detail: unknown): string {
  if (typeof detail !== 'object' || detail === null) return ''
  const record = detail as Record<string, unknown>
  const allowed = record['allowed_models']
  if (Array.isArray(allowed) && allowed.length > 0) {
    return `允许的模型：${allowed.map(String).join('、')}`
  }
  const reason = record['reason']
  if (typeof reason === 'string' && reason) return `原因：${reason}`
  return ''
}

onMounted(load)

// ---- 区块 1：Provider 注册 / 编辑 ----

const MODELS_SEPARATOR = ','

interface ProviderFormState {
  name: string
  kind: AgentProviderKind
  api: 'openai-completions' | 'openai-responses'
  baseUrl: string
  secret: string
  secretClear: boolean
  models: string
  enabled: boolean
}

function blankForm(): ProviderFormState {
  return {
    name: '',
    kind: 'openai_compatible',
    api: 'openai-responses',
    baseUrl: '',
    secret: '',
    secretClear: false,
    models: '',
    enabled: true,
  }
}

/** 编辑模式：表单初始值（PATCH diff 基准行）。 */
const editing = ref<AdminAgentProviderOut | null>(null)
const form = ref<ProviderFormState>(blankForm())
const formMode = ref<'closed' | 'create' | 'edit'>('closed')
const formError = ref('')
const formSubmitting = ref(false)

function parseModels(raw: string): string[] {
  return raw
    .split(MODELS_SEPARATOR)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function openCreate(): void {
  editing.value = null
  form.value = blankForm()
  formError.value = ''
  formMode.value = 'create'
}

function openEdit(row: AdminAgentProviderOut): void {
  editing.value = row
  form.value = {
    name: row.name,
    kind: row.kind,
    api: row.api,
    baseUrl: row.base_url ?? '',
    secret: '',
    secretClear: false,
    models: row.allowed_models.join(', '),
    enabled: row.enabled,
  }
  formError.value = ''
  formMode.value = 'edit'
}

function closeForm(): void {
  formMode.value = 'closed'
  editing.value = null
  formError.value = ''
}

function buildCreatePayload(): AdminAgentProviderCreatePayload {
  const payload: AdminAgentProviderCreatePayload = {
    name: form.value.name.trim(),
    kind: form.value.kind,
    api: form.value.api,
    allowed_models: parseModels(form.value.models),
    enabled: form.value.enabled,
  }
  if (form.value.baseUrl.trim()) payload.base_url = form.value.baseUrl.trim()
  if (form.value.secret) payload.secret = form.value.secret
  return payload
}

/** PATCH 语义：只提交相对基准行发生变更的字段。 */
function buildPatchPayload(): AdminAgentProviderPatchPayload {
  const original = editing.value
  if (original === null) return {}
  const payload: AdminAgentProviderPatchPayload = {}
  const name = form.value.name.trim()
  if (name !== original.name) payload.name = name
  if (form.value.api !== original.api) payload.api = form.value.api
  const baseUrl = form.value.baseUrl.trim() || null
  if (baseUrl !== original.base_url) payload.base_url = baseUrl
  const models = parseModels(form.value.models)
  if (JSON.stringify(models) !== JSON.stringify(original.allowed_models)) {
    payload.allowed_models = models
  }
  if (form.value.enabled !== original.enabled) payload.enabled = form.value.enabled
  // secret 三态：勾选清除 → 空串；输入非空 → 轮换；留空 → 不提交
  if (form.value.secretClear) payload.secret = ''
  else if (form.value.secret) payload.secret = form.value.secret
  return payload
}

async function submitForm(): Promise<void> {
  formError.value = ''
  formSubmitting.value = true
  try {
    if (formMode.value === 'create') {
      await createProvider(buildCreatePayload())
    } else {
      const payload = buildPatchPayload()
      if (Object.keys(payload).length === 0) {
        closeForm()
        return
      }
      await updateProvider(editing.value?.id ?? 0, payload)
    }
    closeForm()
    await load()
  } catch (error) {
    formError.value = describeError(error)
  } finally {
    formSubmitting.value = false
  }
}

// ---- 区块 2：平台默认模型 ----

interface PdKindState {
  providerId: string
  model: string
}

const pdForm = ref<Record<'assistant' | 'steward', PdKindState>>({
  assistant: { providerId: '', model: '' },
  steward: { providerId: '', model: '' },
})
const pdError = ref('')
const pdSuccess = ref('')
const pdSubmitting = ref(false)

const enabledProviders = computed(() => providers.value.filter((row) => row.enabled))

function syncPdForm(): void {
  const defaults = platformDefaults.value
  for (const kind of ['assistant', 'steward'] as const) {
    const current = defaults?.[kind] ?? null
    pdForm.value[kind] = {
      providerId: current === null ? '' : String(current.provider_id),
      model: current?.model ?? '',
    }
  }
}

function onPdProviderChange(kind: 'assistant' | 'steward'): void {
  // Provider 变更时 model 联动重置：旧 model 大概率不在新 allowlist 内
  pdForm.value[kind].model = ''
}

function pdModelsFor(kind: 'assistant' | 'steward'): string[] {
  const providerId = Number(pdForm.value[kind].providerId)
  const row = providers.value.find((item) => item.id === providerId)
  return row?.allowed_models ?? []
}

async function savePlatformDefaults(): Promise<void> {
  pdError.value = ''
  pdSuccess.value = ''
  pdSubmitting.value = true
  try {
    const payload: Record<string, unknown> = {}
    for (const kind of ['assistant', 'steward'] as const) {
      const state = pdForm.value[kind]
      const providerId = Number(state.providerId)
      payload[kind] =
        state.providerId && state.model && Number.isFinite(providerId)
          ? { provider_id: providerId, model: state.model }
          : null
    }
    platformDefaults.value = await putPlatformDefaults(
      payload as AdminAgentPlatformDefaultsPayload,
    )
    syncPdForm()
    pdSuccess.value = '平台默认已保存'
  } catch (error) {
    pdError.value = describeError(error)
  } finally {
    pdSubmitting.value = false
  }
}

function describeDefault(kind: 'assistant' | 'steward'): string {
  const current = platformDefaults.value?.[kind] ?? null
  if (current === null) return '未设置（新空间将提示联系管理员）'
  const provider = providers.value.find((row) => row.id === current.provider_id)
  return `${provider?.name ?? `Provider #${current.provider_id}`} · ${current.model}`
}

// ---- 区块 3：空间设置只读排查 ----

const lookupSpaceId = ref('')
const lookupResult = ref<AdminSpaceProviderSettingsOut | null>(null)
const lookupError = ref('')
const lookupSubmitting = ref(false)

function describeRow(setting: AdminSpaceProviderSettingsOut['settings']['assistant']): string {
  if (setting === null) return '无显式行（继承平台默认）'
  if (!setting.enabled) return '已显式停用（该 Agent 无模型）'
  const provider = providers.value.find((row) => row.id === setting.provider_id)
  const parts = [
    `${provider?.name ?? `Provider #${setting.provider_id ?? '?'}`} · ${setting.model ?? '?'}`,
    setting.cloud_allowed ? '已同意云执行' : '未同意云执行',
  ]
  return parts.join('，')
}

async function lookupSpace(): Promise<void> {
  lookupError.value = ''
  lookupResult.value = null
  const spaceId = Number(lookupSpaceId.value)
  if (!Number.isInteger(spaceId) || spaceId <= 0) {
    lookupError.value = '请输入有效的空间 ID'
    return
  }
  lookupSubmitting.value = true
  try {
    lookupResult.value = await getSpaceProviderSettings(spaceId)
  } catch (error) {
    lookupError.value = describeError(error)
  } finally {
    lookupSubmitting.value = false
  }
}
</script>

<template>
  <div>
    <h1 class="ag-page-title">Agent 模型治理</h1>
    <p class="ag-page-subtitle">
      维护 Provider 注册表与平台默认模型；空间级选择与云同意由空间所有者在家庭端完成。
    </p>

    <!-- 区块 1：Provider 注册表 -->
    <section class="ag-card" aria-labelledby="provider-registry-title">
      <h2 id="provider-registry-title" class="ag-heading-section">Provider 注册表</h2>
      <PageState v-if="listState !== 'ready'" :state="listState" empty-text="暂无 Provider" @retry="load" />
      <template v-else>
        <p v-if="listError" class="form-error" role="alert">{{ listError }}</p>
        <div class="ag-toolbar">
          <button
            type="button"
            class="ag-tag ag-btn-primary"
            data-testid="provider-open-create"
            @click="openCreate"
          >
            注册 Provider
          </button>
        </div>
        <div v-if="providers.length === 0">
          <PageState state="empty" empty-text="尚无注册的 Provider" />
        </div>
        <div v-else class="ag-table-wrap">
          <table class="ag-table" data-testid="provider-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>名称</th>
                <th>类型</th>
                <th>接口</th>
                <th>允许模型</th>
                <th>密钥</th>
                <th>状态</th>
                <th>更新时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in providers" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.name }}</td>
                <td>{{ row.kind === 'local' ? '本地' : '云(openai_compatible)' }}</td>
                <td>{{ row.api }}</td>
                <td>{{ row.allowed_models.join('、') }}</td>
                <td>
                  <span class="ag-tag">{{ row.has_secret ? '已配置' : '无' }}</span>
                </td>
                <td>
                  <span class="ag-tag" :class="{ 'ag-tag-healthy': row.enabled }">
                    {{ row.enabled ? '启用' : '停用' }}
                  </span>
                </td>
                <td>{{ row.updated_at }}</td>
                <td>
                  <button
                    type="button"
                    class="ag-tag"
                    :data-testid="`provider-edit-${row.id}`"
                    @click="openEdit(row)"
                  >
                    编辑
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <form
          v-if="formMode !== 'closed'"
          class="provider-form"
          novalidate
          data-testid="provider-form"
          @submit.prevent="submitForm"
        >
          <h3 class="ag-heading-sm">
            {{ formMode === 'create' ? '注册 Provider' : `编辑 Provider #${editing?.id}` }}
          </h3>
          <div class="provider-form-grid">
            <div class="form-field">
              <label for="provider-name">名称</label>
              <input id="provider-name" v-model="form.name" type="text" data-testid="provider-name" />
            </div>
            <div class="form-field">
              <label for="provider-kind">类型</label>
              <select
                id="provider-kind"
                v-model="form.kind"
                :disabled="formMode === 'edit'"
                data-testid="provider-kind"
              >
                <option value="openai_compatible">openai_compatible（云）</option>
                <option value="local">local（本地）</option>
              </select>
            </div>
            <div class="form-field">
              <label for="provider-api">接口</label>
              <select id="provider-api" v-model="form.api" data-testid="provider-api">
                <option value="openai-responses">openai-responses</option>
                <option value="openai-completions">openai-completions</option>
              </select>
            </div>
            <div class="form-field">
              <label for="provider-base-url">Base URL（openai_compatible 必填）</label>
              <input
                id="provider-base-url"
                v-model="form.baseUrl"
                type="text"
                placeholder="https://api.example.com/v1"
                data-testid="provider-base-url"
              />
            </div>
            <div class="form-field">
              <label for="provider-models">允许模型（逗号分隔）</label>
              <input
                id="provider-models"
                v-model="form.models"
                type="text"
                placeholder="gpt-5.6-sol, gpt-5.6-mini"
                data-testid="provider-models"
              />
            </div>
            <div class="form-field">
              <label for="provider-secret">
                {{ formMode === 'create' ? '密钥（只写，永不再显示）' : '密钥（留空=不变）' }}
              </label>
              <input
                id="provider-secret"
                v-model="form.secret"
                type="password"
                autocomplete="new-password"
                :disabled="form.secretClear"
                data-testid="provider-secret"
              />
              <label v-if="formMode === 'edit'" class="provider-secret-clear">
                <input v-model="form.secretClear" type="checkbox" data-testid="provider-secret-clear" />
                清除密钥
              </label>
            </div>
            <div class="form-field">
              <label for="provider-enabled">启用</label>
              <select id="provider-enabled" v-model="form.enabled" data-testid="provider-enabled">
                <option :value="true">启用</option>
                <option :value="false">停用</option>
              </select>
            </div>
          </div>
          <p v-if="formError" class="form-error" role="alert" data-testid="provider-form-error">
            {{ formError }}
          </p>
          <div class="form-actions">
            <button
              type="submit"
              class="ag-tag ag-btn-primary"
              :disabled="formSubmitting"
              data-testid="provider-form-submit"
            >
              {{ formSubmitting ? '提交中…' : formMode === 'create' ? '注册' : '保存变更' }}
            </button>
            <button type="button" class="ag-tag" data-testid="provider-form-cancel" @click="closeForm">
              取消
            </button>
          </div>
        </form>
      </template>
    </section>

    <!-- 区块 2：平台默认模型 -->
    <section class="ag-card" aria-labelledby="platform-defaults-title">
      <h2 id="platform-defaults-title" class="ag-heading-section">平台默认模型</h2>
      <p class="ag-page-subtitle">
        仅决定通道与模型档位；空间是否同意云端执行仍由空间所有者决定。清除某维度 =
        新空间在该维度无可用模型（提示联系管理员）。
      </p>
      <div class="pd-grid">
        <div v-for="kind in (['assistant', 'steward'] as const)" :key="kind" class="pd-kind">
          <h3 class="ag-heading-sm">{{ kind === 'assistant' ? '助手（assistant）' : '管家（steward）' }}</h3>
          <p class="pd-current" :data-testid="`pd-current-${kind}`">当前：{{ describeDefault(kind) }}</p>
          <div class="form-field">
            <label :for="`pd-${kind}-provider`">默认 Provider</label>
            <select
              :id="`pd-${kind}-provider`"
              v-model="pdForm[kind].providerId"
              :data-testid="`pd-${kind}-provider`"
              @change="onPdProviderChange(kind)"
            >
              <option value="">（未设置）</option>
              <option v-for="row in enabledProviders" :key="row.id" :value="String(row.id)">
                {{ row.name }}（{{ row.kind === 'local' ? '本地' : '云' }}）
              </option>
            </select>
          </div>
          <div class="form-field">
            <label :for="`pd-${kind}-model`">默认模型</label>
            <select
              :id="`pd-${kind}-model`"
              v-model="pdForm[kind].model"
              :disabled="!pdForm[kind].providerId"
              :data-testid="`pd-${kind}-model`"
            >
              <option value="">（未设置）</option>
              <option v-for="model in pdModelsFor(kind)" :key="model" :value="model">
                {{ model }}
              </option>
            </select>
          </div>
        </div>
      </div>
      <p v-if="pdError" class="form-error" role="alert" data-testid="pd-error">{{ pdError }}</p>
      <p v-if="pdSuccess" class="form-success" role="status" data-testid="pd-success">{{ pdSuccess }}</p>
      <div class="form-actions">
        <button
          type="button"
          class="ag-tag ag-btn-primary"
          :disabled="pdSubmitting"
          data-testid="pd-save"
          @click="savePlatformDefaults"
        >
          {{ pdSubmitting ? '保存中…' : '保存平台默认' }}
        </button>
      </div>
    </section>

    <!-- 区块 3：空间设置只读排查 -->
    <section class="ag-card" aria-labelledby="space-lookup-title">
      <h2 id="space-lookup-title" class="ag-heading-section">空间模型设置排查</h2>
      <p class="ag-page-subtitle">只读查看某空间两个 Agent 维度的行级设置与平台默认状态。</p>
      <form class="ag-inline-form" novalidate @submit.prevent="lookupSpace">
        <input
          v-model="lookupSpaceId"
          type="number"
          min="1"
          placeholder="空间 ID"
          aria-label="空间 ID"
          data-testid="space-lookup-input"
        />
        <button type="submit" class="ag-tag" :disabled="lookupSubmitting" data-testid="space-lookup-submit">
          查询
        </button>
      </form>
      <p v-if="lookupError" class="form-error" role="alert" data-testid="space-lookup-error">
        {{ lookupError }}
      </p>
      <div v-if="lookupResult" class="ag-table-wrap" data-testid="space-lookup-result">
        <table class="ag-table">
          <thead>
            <tr>
              <th>Agent 维度</th>
              <th>空间行级设置</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="kind in (['assistant', 'steward'] as const)" :key="kind">
              <td>{{ kind === 'assistant' ? '助手（assistant）' : '管家（steward）' }}</td>
              <td>{{ describeRow(lookupResult.settings[kind]) }}</td>
            </tr>
          </tbody>
        </table>
        <p class="pd-current" data-testid="space-lookup-platform-default">
          平台默认：助手 {{ lookupResult.platform_default.assistant?.model ?? '未设置' }} ·
          管家 {{ lookupResult.platform_default.steward?.model ?? '未设置' }}
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 仅组件内补充布局；设计 token 与 ag-* 体系全部来自 main.css（A4：不改 main.css） */
.provider-form {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--ag-border, rgba(148, 163, 184, 0.25));
}

.provider-form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px 16px;
}

.provider-secret-clear {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ag-text-secondary, #94a3b8);
}

.pd-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 12px;
}

.pd-current {
  font-size: 12px;
  color: var(--ag-text-secondary, #94a3b8);
}
</style>
