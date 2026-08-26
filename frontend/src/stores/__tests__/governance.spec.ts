import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as governanceApi from '@/api/governance'
import { useGovernanceStore } from '@/stores/governance'
import type { ClaimDispute, DataRightRequest, FactReview } from '@/types/api'

vi.mock('@/api/governance', () => ({
  confirmIdentity: vi.fn(),
  fetchFactReviews: vi.fn(),
  decideFactReview: vi.fn(),
  fetchDataRights: vi.fn(),
  requestExport: vi.fn(),
  requestCorrection: vi.fn(),
  requestDeletion: vi.fn(),
  executeDelete: vi.fn(),
  downloadExport: vi.fn(),
  raiseClaimDispute: vi.fn(),
  fetchMyClaimDisputes: vi.fn(),
  withdrawClaimDispute: vi.fn(),
}))

const mockedFetchReviews = vi.mocked(governanceApi.fetchFactReviews)
const mockedDecide = vi.mocked(governanceApi.decideFactReview)
const mockedFetchRights = vi.mocked(governanceApi.fetchDataRights)
const mockedRequestExport = vi.mocked(governanceApi.requestExport)

function makeReview(overrides: Partial<FactReview> = {}): FactReview {
  return {
    id: 1,
    item_type: 'name',
    item_ref_json: { field: 'name', value: '张三' },
    status: 'proposed',
    decided_at: null,
    created_at: '2026-08-26T00:00:00',
    ...overrides,
  }
}

describe('governance store（v2 F-1/F-5）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loadFactReviews：拉取清单并派生 pending 列表', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview(), makeReview({ id: 2, status: 'confirmed' })])
    const store = useGovernanceStore()

    await store.loadFactReviews()
    expect(store.pendingFactReviews).toHaveLength(1)
  })

  it('decideReviewItem：决议后本地同步清单项', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview()])
    const store = useGovernanceStore()
    await store.loadFactReviews()

    mockedDecide.mockResolvedValue(makeReview({ status: 'confirmed', decided_at: '2026-08-26T01:00:00' }))
    await store.decideReviewItem(1, 'confirmed')

    expect(mockedDecide).toHaveBeenCalledWith(1, 'confirmed', null)
    expect(store.pendingFactReviews).toHaveLength(0)
  })

  it('requestExport：创建后刷新申请历史（F-5）', async () => {
    const created: DataRightRequest = {
      id: 10,
      type: 'export',
      status: 'pending',
      scope: 'self',
      policy_version: 'v2',
      payload_json: null,
      expires_at: null,
      created_at: '2026-08-26T00:00:00',
      finished_at: null,
    }
    mockedRequestExport.mockResolvedValue(created)
    mockedFetchRights.mockResolvedValue([created])
    const store = useGovernanceStore()

    const row = await store.requestExport()
    expect(row.id).toBe(10)
    expect(mockedFetchRights).toHaveBeenCalled()
    expect(store.dataRights).toHaveLength(1)
  })

  it('clear：清空确档/数据权利/争议缓存', async () => {
    mockedFetchReviews.mockResolvedValue([makeReview()])
    const dispute: ClaimDispute = {
      id: 3,
      profile_id: 1,
      raised_by_account_id: 1,
      evidence_json: {},
      status: 'open',
      resolution_note: null,
      created_at: '2026-08-26T00:00:00',
      resolved_at: null,
    }
    const mockedDisputes = vi.mocked(governanceApi.fetchMyClaimDisputes)
    mockedDisputes.mockResolvedValue([dispute])
    const store = useGovernanceStore()
    await store.loadFactReviews()
    await store.loadDisputes()
    expect(store.factReviews).toHaveLength(1)
    expect(store.disputes).toHaveLength(1)

    store.clear()
    expect(store.factReviews).toHaveLength(0)
    expect(store.disputes).toHaveLength(0)
  })
})
