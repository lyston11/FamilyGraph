<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { NAlert, NButton, NSelect, NSpin, NSwitch, useMessage } from 'naive-ui'

import { ApiError } from '@/api/errors'
import {
  fetchSpaceModelSettings,
  resetSpaceModelSetting,
  updateSpaceModelSetting,
} from '@/api/spaceModelSettings'
import type {
  AgentConfigKind,
  AgentModelCatalogEntry,
  SpaceAgentSetting,
  SpaceModelSettings,
} from '@/types/agent'

/**
 * 空间模型设置面板（09-06 治理迁移，design §6.1；仅空间管理员可用）。
 *
 * assistant 与 steward 两个独立区块，各含：当前状态（继承平台默认 / 自选 /
 * 显式停用）、模型选择（管理员允许目录内 provider+model 两级联动）、云同意
 * 开关（仅云 Provider 可见）、恢复平台默认（DELETE）与显式停用（enabled=false）。
 * 平台默认存在时提供"同意并启用平台默认"一键（复制默认行 + cloud_allowed=true）。
 *
 * 权限与解析语义都在服务端（api/space_model_settings.py + services/agent_provider）：
 * 目录只含管理员允许的 enabled Provider；平台默认只决定通道与档位，云同意仍归
 * 空间（开关默认关）。响应无任何密钥形态字段。
 */
const props = defineProps<{ spaceId: number }>()

const message = useMessage()

const loading = ref(true)
const loadError = ref(false)
const settings = ref<SpaceModelSettings | null>(null)
const savingKind = ref<AgentConfigKind | null>(null)

const KIND_LABELS: Record<AgentConfigKind, string> = {
  assistant: '助手（assistant）',
  steward: '管家（steward）',
}
const KINDS: readonly AgentConfigKind[] = ['assistant', 'steward']

interface KindFormState {
  providerId: number | null
  model: string | null
  cloudAllowed: boolean
  assistCandidate: boolean
  assistRanking: boolean
  assistExplanation: boolean
}

function emptyForm(): KindFormState {
  return {
    providerId: null,
    model: null,
    cloudAllowed: false,
    assistCandidate: false,
    assistRanking: false,
    assistExplanation: false,
  }
}

const forms = ref<Record<AgentConfigKind, KindFormState>>({
  assistant: emptyForm(),
  steward: emptyForm(),
})

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    settings.value = await fetchSpaceModelSettings(props.spaceId)
    syncForms()
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

/** 表单初值 = 当前行级设置；无行 = 预填平台默认（未同意云），便于一键保存 */
function syncForms(): void {
  const data = settings.value
  if (data === null) return
  for (const kind of KINDS) {
    const row: SpaceAgentSetting | null = data.settings[kind]
    const fallbackDefault = data.platform_default[kind]
    if (row !== null && row.enabled) {
      forms.value[kind] = {
        providerId: row.provider_id,
        model: row.model,
        cloudAllowed: row.cloud_allowed,
        assistCandidate: row.assist_candidate,
        assistRanking: row.assist_ranking,
        assistExplanation: row.assist_explanation,
      }
    } else if (fallbackDefault !== null) {
      forms.value[kind] = {
        ...emptyForm(),
        providerId: fallbackDefault.provider_id,
        model: fallbackDefault.model,
      }
    } else {
      forms.value[kind] = emptyForm()
    }
  }
}

onMounted(load)

const catalog = computed<AgentModelCatalogEntry[]>(() => settings.value?.catalog ?? [])

function catalogEntry(providerId: number | null): AgentModelCatalogEntry | null {
  if (providerId === null) return null
  return catalog.value.find((entry) => entry.provider_id === providerId) ?? null
}

function providerOptions() {
  return catalog.value.map((entry) => ({ label: entry.name, value: entry.provider_id }))
}

function modelOptions(kind: AgentConfigKind) {
  const entry = catalogEntry(forms.value[kind].providerId)
  return (entry?.models ?? []).map((model) => ({ label: model, value: model }))
}

function selectedIsCloud(kind: AgentConfigKind): boolean {
  return catalogEntry(forms.value[kind].providerId)?.kind === 'openai_compatible'
}

function platformDefaultFor(kind: AgentConfigKind) {
  return settings.value?.platform_default[kind] ?? null
}

function rowFor(kind: AgentConfigKind): SpaceAgentSetting | null {
  return settings.value?.settings[kind] ?? null
}

/** 当前状态文案：显式停用 / 自选（含云同意）/ 继承平台默认 / 未配置 */
function statusText(kind: AgentConfigKind): string {
  const row = rowFor(kind)
  if (row !== null && !row.enabled) return '已显式停用：该 Agent 在本空间无模型'
  if (row !== null && row.enabled) {
    const entry = catalogEntry(row.provider_id)
    const providerLabel = entry?.name ?? `Provider #${row.provider_id ?? '?'}`
    const cloud = row.cloud_allowed ? '，已同意云端执行' : '，未同意云端执行'
    return `自选：${providerLabel} · ${row.model ?? '?'}${cloud}`
  }
  const fallbackDefault = platformDefaultFor(kind)
  if (fallbackDefault !== null) {
    const entry = catalogEntry(fallbackDefault.provider_id)
    const providerLabel = entry?.name ?? `Provider #${fallbackDefault.provider_id}`
    return `继承平台默认：${providerLabel} · ${fallbackDefault.model}（可在下方确认启用）`
  }
  return '未配置：平台管理员尚未设置该维度的默认模型'
}

function statusKind(kind: AgentConfigKind): 'default' | 'off' | 'unset' {
  const row = rowFor(kind)
  if (row !== null && !row.enabled) return 'off'
  if (row !== null && row.enabled) return 'default'
  return platformDefaultFor(kind) === null ? 'unset' : 'default'
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { allowed_models?: string[] } | undefined
    if (detail !== undefined && Array.isArray(detail.allowed_models) && detail.allowed_models.length > 0) {
      return `${error.message}（允许的模型：${detail.allowed_models.join('、')}）`
    }
    return error.message
  }
  return '操作失败，请稍后重试'
}

async function saveKind(kind: AgentConfigKind, overrides?: Partial<KindFormState>): Promise<void> {
  if (props.spaceId <= 0) return
  const state = { ...forms.value[kind], ...overrides }
  savingKind.value = kind
  try {
    await updateSpaceModelSetting(props.spaceId, {
      agent_kind: kind,
      provider_id: state.providerId,
      model: state.model,
      cloud_allowed: state.cloudAllowed,
      enabled: true,
      ...(kind === 'steward'
        ? {
            assist_candidate: state.assistCandidate,
            assist_ranking: state.assistRanking,
            assist_explanation: state.assistExplanation,
          }
        : {}),
    })
    await load()
    message.success(`${KIND_LABELS[kind]}模型设置已保存`)
  } catch (error) {
    message.error(describeError(error))
  } finally {
    savingKind.value = null
  }
}

function saveSelection(kind: AgentConfigKind): void {
  const state = forms.value[kind]
  if (state.providerId === null || !state.model) {
    message.warning('请先选择 Provider 与模型')
    return
  }
  void saveKind(kind)
}

/** 一键启用平台默认：复制默认行并同意云执行（AC-3 owner 侧闭环） */
function applyPlatformDefault(kind: AgentConfigKind): void {
  const fallbackDefault = platformDefaultFor(kind)
  if (fallbackDefault === null) return
  forms.value[kind] = {
    ...emptyForm(),
    providerId: fallbackDefault.provider_id,
    model: fallbackDefault.model,
    cloudAllowed: true,
  }
  void saveKind(kind, { cloudAllowed: true })
}

async function disableKind(kind: AgentConfigKind): Promise<void> {
  if (props.spaceId <= 0) return
  savingKind.value = kind
  try {
    await updateSpaceModelSetting(props.spaceId, {
      agent_kind: kind,
      enabled: false,
      provider_id: null,
      model: null,
    })
    await load()
    message.success(`${KIND_LABELS[kind]}已停用（恢复平台默认继承请点"恢复平台默认"）`)
  } catch (error) {
    message.error(describeError(error))
  } finally {
    savingKind.value = null
  }
}

async function resetKind(kind: AgentConfigKind): Promise<void> {
  if (props.spaceId <= 0) return
  savingKind.value = kind
  try {
    await resetSpaceModelSetting(props.spaceId, kind)
    await load()
    message.success(`${KIND_LABELS[kind]}已恢复平台默认继承`)
  } catch (error) {
    message.error(describeError(error))
  } finally {
    savingKind.value = null
  }
}
</script>

<template>
  <div class="model-settings" data-test="model-settings-panel">
    <NSpin :show="loading">
      <NAlert v-if="loadError" type="warning" :show-icon="true">
        模型设置暂时无法加载，请稍后重试。
        <NButton size="small" secondary class="retry-action" @click="load">重新加载</NButton>
      </NAlert>

      <template v-else-if="settings !== null">
        <p class="panel-hint">
          模型由平台管理员统一注册；此处为两个助手分别选择模型。选择云端模型后需打开"同意云端执行"，
          云端执行才会生效。
        </p>

        <div v-for="kind in KINDS" :key="kind" class="kind-block" :data-test="`model-settings-${kind}`">
          <div class="kind-head">
            <h3 class="kind-title">{{ KIND_LABELS[kind] }}</h3>
            <span
              class="kind-status"
              :class="`kind-status--${statusKind(kind)}`"
              :data-test="`model-status-${kind}`"
            >
              {{ statusText(kind) }}
            </span>
          </div>

          <div class="kind-controls">
            <NSelect
              v-model:value="forms[kind].providerId"
              class="control-provider"
              :options="providerOptions()"
              placeholder="选择 Provider"
              size="small"
              :data-test="`model-provider-select-${kind}`"
              @update:value="forms[kind].model = null"
            />
            <NSelect
              v-model:value="forms[kind].model"
              class="control-model"
              :options="modelOptions(kind)"
              placeholder="选择模型"
              size="small"
              :disabled="forms[kind].providerId === null"
              :data-test="`model-select-${kind}`"
            />
            <label
              v-if="selectedIsCloud(kind)"
              class="cloud-consent"
              :data-test="`cloud-consent-${kind}`"
            >
              <NSwitch v-model:value="forms[kind].cloudAllowed" size="small" />
              <span>同意云端执行</span>
            </label>
          </div>

          <!-- 09-06 模型辅助层：管家三类辅助开关（默认关；assistant 不涉及） -->
          <div v-if="kind === 'steward'" class="assist-flags" :data-test="`assist-flags-steward`">
            <label class="assist-flag">
              <NSwitch v-model:value="forms[kind].assistCandidate" size="small" />
              <span>候选补全</span>
            </label>
            <label class="assist-flag">
              <NSwitch v-model:value="forms[kind].assistRanking" size="small" />
              <span>推荐排序</span>
            </label>
            <label class="assist-flag">
              <NSwitch v-model:value="forms[kind].assistExplanation" size="small" />
              <span>卡片解释</span>
            </label>
          </div>

          <div class="kind-actions">
            <NButton
              size="small"
              type="primary"
              secondary
              :loading="savingKind === kind"
              :disabled="forms[kind].providerId === null || !forms[kind].model"
              :data-test="`model-save-${kind}`"
              @click="saveSelection(kind)"
            >
              保存选择
            </NButton>
            <NButton
              v-if="platformDefaultFor(kind) !== null && rowFor(kind) === null"
              size="small"
              type="primary"
              secondary
              :loading="savingKind === kind"
              :data-test="`model-apply-default-${kind}`"
              @click="applyPlatformDefault(kind)"
            >
              同意并启用平台默认
            </NButton>
            <NButton
              v-if="rowFor(kind) !== null"
              size="small"
              :loading="savingKind === kind"
              :data-test="`model-reset-${kind}`"
              @click="resetKind(kind)"
            >
              恢复平台默认
            </NButton>
            <NButton
              v-if="rowFor(kind) !== null && rowFor(kind)?.enabled"
              size="small"
              type="warning"
              secondary
              :loading="savingKind === kind"
              :data-test="`model-disable-${kind}`"
              @click="disableKind(kind)"
            >
              停用
            </NButton>
          </div>
        </div>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.model-settings { display: flex; flex-direction: column; gap: 12px; }
.panel-hint { margin: 0 0 4px; color: var(--fg-ink-secondary); font-size: 12px; line-height: 1.6; }
.retry-action { margin-left: 8px; }

.kind-block {
  padding: 14px;
  border: 1px solid var(--fg-line);
  border-radius: var(--fg-radius-card);
  background: var(--fg-surface);
}
.kind-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin-bottom: 10px; }
.kind-title { margin: 0; font-size: 14px; color: var(--fg-ink); }
.kind-status { font-size: 12px; color: var(--fg-ink-secondary); }
.kind-status--off { color: var(--fg-status-disputed); }
.kind-status--unset { color: var(--fg-status-disputed); }

.kind-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 10px; }
.control-provider { flex: 1 1 180px; min-width: 0; }
.control-model { flex: 1 1 160px; min-width: 0; }
.cloud-consent { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fg-ink-secondary); }

.assist-flags { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 10px; }
.assist-flag { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--fg-ink-secondary); }

.kind-actions { display: flex; flex-wrap: wrap; gap: 8px; }
</style>
