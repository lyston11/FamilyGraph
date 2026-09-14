import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '@/api/stewardSuggestions'
import * as notificationsApi from '@/api/notifications'
import { useNotificationsStore } from '@/stores/notifications'
import { useStewardSuggestionsStore } from '@/stores/stewardSuggestions'
import type { NotificationsSnapshot, SuggestionItem, SuggestionSubmitProposalResult } from '@/types/api'

vi.mock('@/api/stewardSuggestions', () => ({
  fetchSuggestions: vi.fn(), fetchSuggestionDetail: vi.fn(),
  dismissSuggestion: vi.fn(), submitSuggestion: vi.fn(),
}))
vi.mock('@/api/notifications', () => ({ fetchNotifications: vi.fn() }))

function suggestion(overrides: Partial<SuggestionItem> = {}): SuggestionItem {
  return {
    id: 70, space_id: 7, kind: 'relation_proposal', origin: 'model', state: 'proposed',
    revision: 1, evidence_hash: 'evidence', subject_user_id: 1, object_user_id: 2,
    subject_name: '我', object_name: '家人', value: {}, presentation: null,
    evidence_summary: { fact_count: 20, facts: [] },
    allowed_actions: ['open_details', 'submit', 'dismiss'], expires_at: null, created_at: '',
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('Steward suggestion current detail and action state', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    vi.mocked(api.fetchSuggestions).mockResolvedValue({ space_id: 7, items: [], next_cursor: null })
    vi.mocked(notificationsApi.fetchNotifications).mockResolvedValue({
      data: { space_id: 7, items: [], unread_count: 0 }, etag: null,
    })
  })

  it.each(['clear', 'clearSpace'] as const)('%s discards late detail without returning it to the caller', async (clear) => {
    const pending = deferred<SuggestionItem>()
    vi.mocked(api.fetchSuggestionDetail).mockReturnValue(pending.promise)
    const store = useStewardSuggestionsStore()
    const request = store.loadDetail(7, 70)
    if (clear === 'clear') store.clear()
    else store.clearSpace(7)
    pending.resolve(suggestion())
    expect(await request).toBeNull()
    expect(store.detailFor(7, 70)).toBeNull()
  })

  it('reopens by revalidating current state even when detail was cached', async () => {
    vi.mocked(api.fetchSuggestionDetail)
      .mockResolvedValueOnce(suggestion())
      .mockResolvedValueOnce(suggestion({ state: 'expired', allowed_actions: ['open_details'] }))
    const store = useStewardSuggestionsStore()
    await store.loadDetail(7, 70)
    const reopened = await store.loadDetail(7, 70)
    expect(api.fetchSuggestionDetail).toHaveBeenCalledTimes(2)
    expect(reopened?.state).toBe('expired')
    expect(reopened?.allowed_actions).toEqual(['open_details'])
  })

  it('a superseded detail request cannot overwrite the newer response', async () => {
    const pending = deferred<SuggestionItem>()
    vi.mocked(api.fetchSuggestionDetail)
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(suggestion({ state: 'resolved', revision: 2, allowed_actions: ['open_details'] }))
    const store = useStewardSuggestionsStore()
    const older = store.loadDetail(7, 70)
    await store.loadDetail(7, 70)
    pending.resolve(suggestion())
    expect(await older).toBeNull()
    expect(store.detailFor(7, 70)?.state).toBe('resolved')
  })

  it('submit returns the actual proposal and updates detail, list and notifications', async () => {
    const result: SuggestionSubmitProposalResult = {
      suggestion: suggestion({ state: 'submitted', revision: 2, allowed_actions: ['open_details'] }),
      linked_proposal: { source_fact_id: 90, state: 'proposed', revision: 1, fact_type: 'spouse' },
      pending_confirmations: [{ account_id: 4 }],
    }
    vi.mocked(api.submitSuggestion).mockResolvedValue(result)
    vi.mocked(api.fetchSuggestions).mockResolvedValue({ space_id: 7, items: [result.suggestion], next_cursor: null })
    const staleNotifications = deferred<NotificationsSnapshot | null>()
    vi.mocked(notificationsApi.fetchNotifications).mockReturnValueOnce(staleNotifications.promise)
    const notifications = useNotificationsStore()
    const oldRead = notifications.load(7)
    const store = useStewardSuggestionsStore()

    expect(await store.submit(7, 70, { expected_revision: 1, evidence_hash: 'evidence', confirm: true }, 'key'))
      .toEqual(result)
    expect(store.detailFor(7, 70)?.allowed_actions).toEqual(['open_details'])
    expect(store.forSpace(7)?.items[0]?.state).toBe('submitted')
    expect(notificationsApi.fetchNotifications).toHaveBeenLastCalledWith(7, null)
    staleNotifications.resolve({ data: { space_id: 7, items: [], unread_count: 99 }, etag: 'old' })
    expect(await oldRead).toBeNull()
    expect(notifications.unreadCountOf(7)).toBe(0)
  })

  it('dismiss reloads the authoritative detail and its allowed actions', async () => {
    const dismissed = suggestion({ state: 'dismissed', revision: 3, allowed_actions: ['open_details'] })
    vi.mocked(api.dismissSuggestion).mockResolvedValue({
      id: 70, state: 'dismissed', revision: 3, dismissed_at: '', cooldown_until: null,
    })
    vi.mocked(api.fetchSuggestionDetail).mockResolvedValue(dismissed)
    const store = useStewardSuggestionsStore()
    expect(await store.dismiss(7, 70, 1)).toEqual(dismissed)
    expect(store.detailFor(7, 70)).toEqual(dismissed)
    expect(api.fetchSuggestions).toHaveBeenCalledWith(7)
    expect(notificationsApi.fetchNotifications).toHaveBeenCalledWith(7, null)
  })

  it('late submission after logout cannot refresh or populate the new session', async () => {
    const pending = deferred<SuggestionSubmitProposalResult>()
    vi.mocked(api.submitSuggestion).mockReturnValue(pending.promise)
    const store = useStewardSuggestionsStore()
    const request = store.submit(7, 70, { expected_revision: 1, evidence_hash: 'evidence', confirm: true }, 'key')
    store.clear()
    pending.resolve({
      suggestion: suggestion({ state: 'submitted' }),
      linked_proposal: { source_fact_id: 90, state: 'proposed', revision: 1, fact_type: 'spouse' },
      pending_confirmations: [],
    })
    expect(await request).toBeNull()
    expect(api.fetchSuggestions).not.toHaveBeenCalled()
    expect(notificationsApi.fetchNotifications).not.toHaveBeenCalled()
    expect(store.detailFor(7, 70)).toBeNull()
  })
})
