import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as governanceApi from '@/api/governance'
import type {
  ClaimDispute,
  DataRightRequest,
  FactReview,
  FactReviewDecision,
  IdentityConfirmResult,
} from '@/types/api'

/**
 * 治理域状态（v2 Foundation）：确档清单 / 数据权利请求 / 认领争议。
 *
 * 路由守卫的判定源是 /me 直出的 profile_status（v2 Gap2）；本 store 仅承载
 * 确档清单内容与治理数据，登出 / 身份切换时由 auth.clearSession 调用 clear()
 * 清空（state-management.md 红线）。
 */
export const useGovernanceStore = defineStore('governance', () => {
  // ---- 确档（F-1）----
  const factReviews = ref<FactReview[]>([])

  const pendingFactReviews = computed(() => factReviews.value.filter((r) => r.status === 'proposed'))

  // ---- 数据权利（F-5）----
  const dataRights = ref<DataRightRequest[]>([])

  // ---- 认领争议 ----
  const disputes = ref<ClaimDispute[]>([])

  function clear(): void {
    factReviews.value = []
    dataRights.value = []
    disputes.value = []
  }

  async function loadFactReviews(): Promise<void> {
    factReviews.value = await governanceApi.fetchFactReviews()
  }

  async function confirmIdentity(): Promise<IdentityConfirmResult> {
    return governanceApi.confirmIdentity()
  }

  async function decideReviewItem(
    reviewId: number,
    decision: FactReviewDecision,
    note?: string | null,
  ): Promise<void> {
    const updated = await governanceApi.decideFactReview(reviewId, decision, note ?? null)
    factReviews.value = factReviews.value.map((r) => (r.id === updated.id ? updated : r))
  }

  async function loadDataRights(): Promise<void> {
    dataRights.value = await governanceApi.fetchDataRights()
  }

  async function requestExport(): Promise<DataRightRequest> {
    const row = await governanceApi.requestExport()
    await loadDataRights()
    return row
  }

  async function requestCorrection(fields: Record<string, unknown>): Promise<DataRightRequest> {
    const row = await governanceApi.requestCorrection(fields)
    await loadDataRights()
    return row
  }

  async function requestDeletion(): Promise<DataRightRequest> {
    const row = await governanceApi.requestDeletion()
    await loadDataRights()
    return row
  }

  async function executeDelete(requestId: number, confirmName: string): Promise<void> {
    await governanceApi.executeDelete(requestId, confirmName)
    await loadDataRights()
  }

  async function downloadExport(requestId: number): Promise<Blob> {
    return governanceApi.downloadExport(requestId)
  }

  async function loadDisputes(): Promise<void> {
    disputes.value = await governanceApi.fetchMyClaimDisputes()
  }

  async function raiseDispute(
    profileId: number,
    evidence: Record<string, unknown>,
  ): Promise<ClaimDispute> {
    const row = await governanceApi.raiseClaimDispute(profileId, evidence)
    await loadDisputes()
    return row
  }

  async function withdrawDispute(disputeId: number): Promise<void> {
    await governanceApi.withdrawClaimDispute(disputeId)
    await loadDisputes()
  }

  return {
    factReviews,
    dataRights,
    disputes,
    pendingFactReviews,
    clear,
    loadFactReviews,
    confirmIdentity,
    decideReviewItem,
    loadDataRights,
    requestExport,
    requestCorrection,
    requestDeletion,
    executeDelete,
    downloadExport,
    loadDisputes,
    raiseDispute,
    withdrawDispute,
  }
})
