import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import {
  dismissSuggestion,
  fetchSuggestionDetail,
  fetchSuggestions,
  submitSuggestion,
} from '@/api/stewardSuggestions'

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

function makeSuggestionFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    space_id: 7,
    kind: 'relation_proposal',
    origin: 'model',
    state: 'proposed',
    revision: 1,
    evidence_hash: 'a'.repeat(64),
    subject_user_id: 10,
    object_user_id: 11,
    subject_name: '甲',
    object_name: '乙',
    value: { fact_type: 'spouse' },
    evidence_summary: { fact_count: 1, facts: [{ fact_id: 5, revision: 2 }] },
    allowed_actions: ['open_details', 'submit', 'dismiss'],
    expires_at: '2026-10-11T00:00:00',
    created_at: '2026-09-11T00:00:00',
    ...overrides,
  }
}

describe('stewardSuggestions decoder', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('decodes a valid page and drops invalid items only', async () => {
    const data = {
      space_id: 7,
      items: [
        makeSuggestionFixture(),
        { id: 'bad' }, // 非法条目：仅丢弃该条
      ],
      next_cursor: null,
    }
    vi.mocked(apiClient.get).mockResolvedValue({ data })
    const page = await fetchSuggestions(7)
    expect(page.items).toHaveLength(1)
    expect(page.items[0]!.allowed_actions).toContain('submit')
  })

  it('throws when the top-level payload is malformed', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: 'nope' } })
    await expect(fetchSuggestions(7)).rejects.toThrow()
  })

  it('submits with an Idempotency-Key header and decodes 202 proposal result', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      status: 202,
      data: {
        suggestion: makeSuggestionFixture({ state: 'submitted', allowed_actions: ['open_details'] }),
        linked_proposal: { source_fact_id: 9, revision: 1, state: 'proposed', fact_type: 'spouse' },
        pending_confirmations: [{ account_id: 2 }],
      },
    })
    const result = await submitSuggestion(
      7,
      1,
      { expected_revision: 1, evidence_hash: 'a'.repeat(64), confirm: true },
      'idem-key-1',
    )
    expect(vi.mocked(apiClient.post).mock.calls[0]![2]).toMatchObject({
      headers: { 'Idempotency-Key': 'idem-key-1' },
    })
    if (!('linked_proposal' in result)) throw new Error('expected proposal result')
    expect(result.linked_proposal.state).toBe('proposed') // 绝不显示为已确认
  })

  it('decodes dismiss result', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { id: 1, state: 'dismissed', revision: 3, dismissed_at: null, cooldown_until: null },
    })
    const result = await dismissSuggestion(7, 1, 2)
    expect(result.state).toBe('dismissed')
    expect(result.revision).toBe(3)
  })

  it('sends target filtering to the server before pagination', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { space_id: 7, items: [], next_cursor: null } })
    await fetchSuggestions(7, null, 50, 'term_preference', 200)
    expect(apiClient.get).toHaveBeenCalledWith('/steward-suggestions', {
      params: { space_id: 7, limit: 50, kind: 'term_preference', target_user_id: 200 },
    })
  })

  it('decodes current linked proposal state on a reopened rejected suggestion', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makeSuggestionFixture({
      state: 'rejected', allowed_actions: ['open_details'],
      linked_proposal: { source_fact_id: 90, state: 'rejected', revision: 3, fact_type: 'spouse' },
      pending_confirmations: [],
    }) })
    const item = await fetchSuggestionDetail(7, 1)
    expect(item.state).toBe('rejected')
    expect(item.linked_proposal?.state).toBe('rejected')
    expect(item.pending_confirmations).toEqual([])
  })

  it('decodes superseded details as a read-only terminal state', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makeSuggestionFixture({
      state: 'superseded', allowed_actions: ['open_details'],
    }) })
    const item = await fetchSuggestionDetail(7, 1)
    expect(item.state).toBe('superseded')
    expect(item.allowed_actions).toEqual(['open_details'])
  })
})
