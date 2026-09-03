import { ref } from 'vue'
import { defineStore } from 'pinia'

import {
  dismissFamilyRecommendation,
  fetchFamilyRecommendations,
} from '@/api/familyRecommendations'
import type { FamilyRecommendationsData } from '@/types/api'

export const useFamilyRecommendationsStore = defineStore('familyRecommendations', () => {
  const bySpace = ref<Map<number, FamilyRecommendationsData>>(new Map())
  const loadingSpaceIds = ref<Set<number>>(new Set())
  const errorBySpace = ref<Map<number, unknown>>(new Map())
  let epoch = 0

  async function load(spaceId: number): Promise<FamilyRecommendationsData | null> {
    const requestEpoch = epoch
    const nextLoading = new Set(loadingSpaceIds.value)
    nextLoading.add(spaceId)
    loadingSpaceIds.value = nextLoading
    try {
      const data = await fetchFamilyRecommendations(spaceId)
      if (requestEpoch !== epoch) return null
      bySpace.value = new Map(bySpace.value).set(spaceId, data)
      return data
    } catch (error) {
      if (requestEpoch === epoch) {
        errorBySpace.value = new Map(errorBySpace.value).set(spaceId, error)
      }
      throw error
    } finally {
      if (requestEpoch === epoch) {
        const done = new Set(loadingSpaceIds.value)
        done.delete(spaceId)
        loadingSpaceIds.value = done
      }
    }
  }

  async function dismiss(spaceId: number, targetUserId: number, category: string): Promise<void> {
    await dismissFamilyRecommendation(spaceId, targetUserId, category)
    await load(spaceId)
  }

  function forSpace(spaceId: number): FamilyRecommendationsData | null {
    return bySpace.value.get(spaceId) ?? null
  }

  function clearSpace(spaceId: number): void {
    epoch += 1
    const next = new Map(bySpace.value)
    next.delete(spaceId)
    bySpace.value = next
  }

  function clear(): void {
    epoch += 1
    bySpace.value = new Map()
    loadingSpaceIds.value = new Set()
    errorBySpace.value = new Map()
  }

  return { bySpace, loadingSpaceIds, errorBySpace, load, dismiss, forSpace, clearSpace, clear }
})
