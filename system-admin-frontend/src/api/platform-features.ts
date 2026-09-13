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
} from '@/types/api'

const SOURCES = ['environment', 'platform', 'deployment'] as const

function decodePlatformFeatureState(raw: unknown): AdminPlatformFeatureState {
  const obj = expectObject(raw, 'platform feature state')
  return {
    memory_enabled: expectBoolean(obj['memory_enabled'], 'memory_enabled'),
    rag_enabled: expectBoolean(obj['rag_enabled'], 'rag_enabled'),
    memory_source: expectLiteral(obj['memory_source'], SOURCES, 'memory_source'),
    rag_source: expectLiteral(obj['rag_source'], SOURCES, 'rag_source'),
    updated_at: expectStringOrNull(obj['updated_at'], 'updated_at'),
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
