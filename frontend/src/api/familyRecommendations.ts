import type { FamilyRecommendationItem, FamilyRecommendationsData } from '@/types/api'

import { isRecord } from './decode'
import { apiClient } from './client'

function decodeItem(value: unknown): FamilyRecommendationItem | null {
  if (
    !isRecord(value) ||
    typeof value.category !== 'string' ||
    typeof value.target_user_id !== 'number' ||
    typeof value.display !== 'object' ||
    value.display === null ||
    typeof value.reason_code !== 'string'
  ) {
    return null
  }
  return {
    category: value.category,
    target_user_id: value.target_user_id,
    display: value.display as Record<string, unknown>,
    reason_code: value.reason_code,
    term: typeof value.term === 'string' ? value.term : null,
    concept_code: typeof value.concept_code === 'string' ? value.concept_code : null,
    path_class: typeof value.path_class === 'string' ? value.path_class : null,
    path_summary:
      Array.isArray(value.path_summary) &&
      value.path_summary.every((entry) => typeof entry === 'string')
        ? value.path_summary
        : null,
    proposed_fact_type: typeof value.proposed_fact_type === 'string' ? value.proposed_fact_type : null,
  }
}

export function decodeFamilyRecommendations(value: unknown): FamilyRecommendationsData {
  if (
    !isRecord(value) ||
    typeof value.space_id !== 'number' ||
    typeof value.view_status !== 'string' ||
    typeof value.view_version !== 'number' ||
    typeof value.generated_from_view_version !== 'number' ||
    typeof value.truncated !== 'boolean' ||
    !Array.isArray(value.items)
  ) {
    throw new Error('亲属推荐响应格式无效')
  }
  return {
    space_id: value.space_id,
    view_status: value.view_status,
    view_version: value.view_version,
    generated_from_view_version: value.generated_from_view_version,
    truncated: value.truncated,
    items: value.items
      .map(decodeItem)
      .filter((item): item is FamilyRecommendationItem => item !== null),
  }
}

export async function fetchFamilyRecommendations(spaceId: number): Promise<FamilyRecommendationsData> {
  const response = await apiClient.get<unknown>('/family-recommendations', {
    params: { space_id: spaceId },
  })
  return decodeFamilyRecommendations(response.data)
}

export async function dismissFamilyRecommendation(
  spaceId: number,
  targetUserId: number,
  category: string,
): Promise<void> {
  await apiClient.post('/family-recommendations/dismiss', {
    space_id: spaceId,
    target_user_id: targetUserId,
    category,
  })
}
