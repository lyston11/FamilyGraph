import { adminRequest } from '@/api/client'
import {
  expectBoolean,
  expectLiteral,
  expectObject,
  expectStringOrNull,
} from '@/api/decode'
import type {
  AdminPlatformFeaturePayload,
  AdminPlatformFeatureState,
  AdminStewardAssistSwitches,
} from '@/types/api'

const SOURCES = ['environment', 'platform', 'deployment'] as const

function decodePlatformFeatureState(raw: unknown): AdminPlatformFeatureState {
  const obj = expectObject(raw, 'platform feature state')
  return {
    memory_enabled: expectBoolean(obj['memory_enabled'], 'memory_enabled'),
    rag_enabled: expectBoolean(obj['rag_enabled'], 'rag_enabled'),
    memory_source: expectLiteral(obj['memory_source'], SOURCES, 'memory_source'),
    rag_source: expectLiteral(obj['rag_source'], SOURCES, 'rag_source'),
    steward_assist: decodeStewardAssist(obj['steward_assist']),
    updated_at: expectStringOrNull(obj['updated_at'], 'updated_at'),
  }
}

function decodeStewardAssist(raw: unknown): AdminStewardAssistSwitches {
  const obj = expectObject(raw, 'steward assist switches')
  return {
    candidate: expectBoolean(obj['candidate'], 'candidate'),
    ranking: expectBoolean(obj['ranking'], 'ranking'),
    explanation: expectBoolean(obj['explanation'], 'explanation'),
    candidate_source: expectLiteral(obj['candidate_source'], SOURCES, 'candidate_source'),
    ranking_source: expectLiteral(obj['ranking_source'], SOURCES, 'ranking_source'),
    explanation_source: expectLiteral(obj['explanation_source'], SOURCES, 'explanation_source'),
  }
}

export function getPlatformFeatures(): Promise<AdminPlatformFeatureState> {
  return adminRequest<unknown>({ method: 'get', url: '/v1/platform-features' }).then(
    decodePlatformFeatureState,
  )
}

export function updatePlatformFeatures(
  payload: AdminPlatformFeaturePayload,
): Promise<AdminPlatformFeatureState> {
  return adminRequest<unknown>({
    method: 'put',
    url: '/v1/platform-features',
    data: payload,
  }).then(decodePlatformFeatureState)
}
