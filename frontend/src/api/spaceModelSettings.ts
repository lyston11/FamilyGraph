import { apiClient } from '@/api/client'

import type {
  AgentConfigKind,
  SpaceAgentSetting,
  SpaceModelSettings,
} from '@/types/agent'

/**
 * 空间模型设置 API（09-06 治理迁移；backend/app/api/space_model_settings.py）。
 *
 * 仅空间管理员可用（服务端按 active space_admin 判定）：
 * - GET 读两 agent 维度行级设置 + 管理员允许目录 + 有效平台默认；
 * - PUT 单维度幂等 upsert（enabled=true 成对必填 provider_id+model）；
 * - DELETE 删行 = 恢复平台默认继承。
 * 响应永无密钥形态字段（目录只含 enabled Provider 元数据）。
 */

export interface SpaceModelSettingPayload {
  agent_kind: AgentConfigKind
  provider_id?: number | null
  model?: string | null
  cloud_allowed?: boolean
  local_required?: boolean
  enabled?: boolean
  /** 仅 steward 维度有意义（assistant 传任一会被 422） */
  assist_candidate?: boolean
  assist_ranking?: boolean
  assist_explanation?: boolean
  assist_terminology?: boolean
  /** 09-13 推测层空间级开关（仅 steward 维度） */
  inferred_tree?: boolean
}

export async function fetchSpaceModelSettings(spaceId: number): Promise<SpaceModelSettings> {
  const { data } = await apiClient.get<SpaceModelSettings>(`/spaces/${spaceId}/model-settings`)
  return data
}

export async function updateSpaceModelSetting(
  spaceId: number,
  payload: SpaceModelSettingPayload,
): Promise<SpaceAgentSetting> {
  const { data } = await apiClient.put<SpaceAgentSetting>(
    `/spaces/${spaceId}/model-settings`,
    payload,
  )
  return data
}

export async function resetSpaceModelSetting(
  spaceId: number,
  agentKind: AgentConfigKind,
): Promise<void> {
  await apiClient.delete(`/spaces/${spaceId}/model-settings/${agentKind}`)
}
