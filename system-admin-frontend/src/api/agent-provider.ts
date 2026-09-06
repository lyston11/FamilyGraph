/**
 * Agent Provider 治理 API（backend/app/api/admin_agent.py，/admin-api/v1/agent/*）。
 *
 * 09-06 治理迁移：Provider 注册表（注册/列表/PATCH 更新）+ 平台默认模型
 * （GET/PUT 全量覆盖）+ 空间设置只读排查视图。信任边界：
 * - secret 只写不读：请求载荷携带 secret，响应只含 has_secret 布尔；
 * - 绝不请求家庭 /api（module-boundary 红线）；全部经统一 adminRequest。
 */

import { adminRequest } from '@/api/client'
import {
  expectArray,
  expectBoolean,
  expectLiteral,
  expectNumber,
  expectNumberOrNull,
  expectObject,
  expectString,
  expectStringArray,
  expectStringOrNull,
} from '@/api/decode'
import type {
  AdminAgentProviderCreatePayload,
  AdminAgentProviderOut,
  AdminAgentProviderPatchPayload,
  AdminAgentPlatformDefaultsPayload,
  AdminSpaceProviderSettingsOut,
  AgentPlatformDefaultKind,
  AgentPlatformDefaultsOut,
  AgentProviderApi,
  AgentProviderKind,
  AgentSpaceSettingRowOut,
} from '@/types/api'

const PROVIDER_KINDS = ['openai_compatible', 'local'] as const
const PROVIDER_APIS = ['openai-completions', 'openai-responses'] as const
const AGENT_KINDS = ['assistant', 'steward'] as const

function decodeProviderKind(value: unknown): AgentProviderKind {
  return expectLiteral(value, PROVIDER_KINDS, 'kind')
}

function decodeProviderApi(value: unknown): AgentProviderApi {
  return expectLiteral(value, PROVIDER_APIS, 'api')
}

function decodeAgentProvider(raw: unknown): AdminAgentProviderOut {
  const obj = expectObject(raw, 'agent provider')
  return {
    id: expectNumber(obj['id'], 'id'),
    name: expectString(obj['name'], 'name'),
    kind: decodeProviderKind(obj['kind']),
    api: decodeProviderApi(obj['api']),
    base_url: expectStringOrNull(obj['base_url'], 'base_url'),
    compat: expectObject(obj['compat'] ?? {}, 'compat'),
    context_window: expectNumber(obj['context_window'], 'context_window'),
    max_tokens: expectNumber(obj['max_tokens'], 'max_tokens'),
    reasoning: expectBoolean(obj['reasoning'], 'reasoning'),
    input_modalities: expectStringArray(obj['input_modalities'], 'input_modalities'),
    thinking_levels: expectStringArray(obj['thinking_levels'], 'thinking_levels'),
    has_secret: expectBoolean(obj['has_secret'], 'has_secret'),
    allowed_models: expectStringArray(obj['allowed_models'], 'allowed_models'),
    enabled: expectBoolean(obj['enabled'], 'enabled'),
    created_at: expectString(obj['created_at'], 'created_at'),
    updated_at: expectString(obj['updated_at'], 'updated_at'),
  }
}

function decodePlatformDefaultKind(raw: unknown): AgentPlatformDefaultKind {
  const obj = expectObject(raw, 'platform default kind')
  return {
    provider_id: expectNumber(obj['provider_id'], 'provider_id'),
    model: expectString(obj['model'], 'model'),
  }
}

function decodePlatformDefaultKindOrNull(raw: unknown): AgentPlatformDefaultKind | null {
  return raw === null ? null : decodePlatformDefaultKind(raw)
}

function decodePlatformDefaults(raw: unknown): AgentPlatformDefaultsOut {
  const obj = expectObject(raw, 'platform defaults')
  return {
    assistant: decodePlatformDefaultKindOrNull(obj['assistant']),
    steward: decodePlatformDefaultKindOrNull(obj['steward']),
    updated_at: expectStringOrNull(obj['updated_at'], 'updated_at'),
  }
}

function decodeSpaceSettingRow(raw: unknown): AgentSpaceSettingRowOut {
  const obj = expectObject(raw, 'space setting row')
  return {
    agent_kind: expectLiteral(obj['agent_kind'], AGENT_KINDS, 'agent_kind'),
    provider_id: expectNumberOrNull(obj['provider_id'], 'provider_id'),
    model: expectStringOrNull(obj['model'], 'model'),
    cloud_allowed: expectBoolean(obj['cloud_allowed'], 'cloud_allowed'),
    local_required: expectBoolean(obj['local_required'], 'local_required'),
    enabled: expectBoolean(obj['enabled'], 'enabled'),
  }
}

function decodeSpaceSettingRowOrNull(raw: unknown): AgentSpaceSettingRowOut | null {
  return raw === null ? null : decodeSpaceSettingRow(raw)
}

function decodeSpaceProviderSettings(raw: unknown): AdminSpaceProviderSettingsOut {
  const obj = expectObject(raw, 'space provider settings')
  const settings = expectObject(obj['settings'], 'settings')
  return {
    space_id: expectNumber(obj['space_id'], 'space_id'),
    settings: {
      assistant: decodeSpaceSettingRowOrNull(settings['assistant']),
      steward: decodeSpaceSettingRowOrNull(settings['steward']),
    },
    platform_default: decodePlatformDefaults(obj['platform_default']),
  }
}

export function listProviders(): Promise<AdminAgentProviderOut[]> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/agent/providers' }).then((raw) =>
    expectArray(raw, 'providers').map((item) => decodeAgentProvider(item)),
  )
}

export function createProvider(payload: AdminAgentProviderCreatePayload): Promise<AdminAgentProviderOut> {
  return adminRequest<unknown>({ method: 'post', url: '/v1/agent/providers', data: payload }).then(
    decodeAgentProvider,
  )
}

export function updateProvider(
  providerId: number,
  payload: AdminAgentProviderPatchPayload,
): Promise<AdminAgentProviderOut> {
  return adminRequest<unknown>({
    method: 'patch',
    url: `/v1/agent/providers/${providerId}`,
    data: payload,
  }).then(decodeAgentProvider)
}

export function getPlatformDefaults(): Promise<AgentPlatformDefaultsOut> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/agent/platform-defaults' }).then(
    decodePlatformDefaults,
  )
}

export function putPlatformDefaults(
  payload: AdminAgentPlatformDefaultsPayload,
): Promise<AgentPlatformDefaultsOut> {
  return adminRequest<unknown>({
    method: 'put',
    url: '/v1/agent/platform-defaults',
    data: payload,
  }).then(decodePlatformDefaults)
}

export function getSpaceProviderSettings(spaceId: number): Promise<AdminSpaceProviderSettingsOut> {
  return adminRequest<unknown>({
    method: 'get',
    url: `/v1/agent/spaces/${spaceId}/provider-settings`,
  }).then(decodeSpaceProviderSettings)
}
