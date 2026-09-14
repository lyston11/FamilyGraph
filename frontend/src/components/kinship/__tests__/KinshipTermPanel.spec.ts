import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as kinshipApi from '@/api/kinship'
import * as suggestionsApi from '@/api/stewardSuggestions'
import * as pfvApi from '@/api/personalFamilyView'
import KinshipTermPanel from '@/components/kinship/KinshipTermPanel.vue'
import { useAuthStore } from '@/stores/auth'
import { useKinshipStore } from '@/stores/kinship'
import { usePersonalFamilyViewStore } from '@/stores/personalFamilyView'
import { useSpacesStore } from '@/stores/spaces'
import type { PersonalFamilyViewSnapshot, SuggestionItem, SuggestionsPage, SuggestionSubmitPreferenceResult } from '@/types/api'
import type { KinshipResolve } from '@/types/kinship'

vi.mock('@/api/kinship', () => ({
  KINSHIP_FLAG_DISABLED: 'KINSHIP_FLAG_DISABLED', resolveKinship: vi.fn(),
  fetchMyTerms: vi.fn(), updateMyTerm: vi.fn(), recordTermUsage: vi.fn(), parseRelationText: vi.fn(),
}))
vi.mock('@/api/stewardSuggestions', () => ({
  fetchSuggestions: vi.fn(), fetchSuggestionDetail: vi.fn(), submitSuggestion: vi.fn(),
  dismissSuggestion: vi.fn(), restoreSuggestionTerm: vi.fn(),
}))
vi.mock('@/api/personalFamilyView', () => ({ fetchPersonalFamilyView: vi.fn() }))
vi.mock('@/api/notifications', () => ({
  fetchNotifications: vi.fn().mockResolvedValue({ data: { space_id: 7, items: [], unread_count: 0 }, etag: null }),
}))

function resolution(spaceId = 7, viewerId = 1, targetId = 2, term = '姥姥'): KinshipResolve {
  return {
    found: true, space_id: spaceId, viewer_user_id: viewerId, target_user_id: targetId,
    term, term_source_level: 'steward', term_entry_id: null, concept_code: 'M_MOTHER',
    path_class: 'direct_line', explanation_structural: null, main_path: [], alt_paths: [],
    fact_state: { confirmed: 2, proposed: 0, disputed: 0, revoked: 0, evidence_fact_ids: [] },
    cache_hit: false, algorithm_version: 'v1',
  }
}

function suggestion(overrides: Partial<SuggestionItem> = {}): SuggestionItem {
  return {
    id: 70, space_id: 7, kind: 'term_preference', origin: 'model', state: 'proposed',
    revision: 2, evidence_hash: 'evidence', subject_user_id: 1, object_user_id: 2,
    subject_name: '我', object_name: '家人', presentation: null,
    value: { term: '姥姥', can_restore: true, projection_revision: 3, semantic_hash: 'current-hash' },
    evidence_summary: { fact_count: 2, facts: [] }, allowed_actions: ['open_details', 'submit'],
    expires_at: null, created_at: '', ...overrides,
  }
}

function page(items: SuggestionItem[], spaceId = 7): SuggestionsPage {
  return { space_id: spaceId, items, next_cursor: null }
}

function snapshot(spaceId: number): PersonalFamilyViewSnapshot {
  return { etag: 'old', data: {
    space_id: spaceId, status: 'current', view_version: 1, computed_at: '', nodes: [],
    edges: [], inferred_edges: [], topology_edges: [], truncated: false, next_cursor: null, stale_reason: null,
  } }
}

function kept(): SuggestionSubmitPreferenceResult {
  return {
    suggestion: suggestion({ state: 'resolved', allowed_actions: ['open_details'] }),
    linked_preference: { term_id: 90, concept_code: 'M_MOTHER', term: '姥姥' },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

async function mountPanel() {
  const pinia = createPinia()
  const auth = useAuthStore(pinia)
  auth.user = { id: 1, name: '我', pin_must_change: false, claim_status: 'claimed', profile_status: 'identity_confirmed' }
  const spaces = useSpacesStore(pinia)
  spaces.currentSpaceId = 7
  const target = ref(2)
  const Harness = defineComponent({ render: () => h(NMessageProvider, () => h(KinshipTermPanel, { memberId: target.value })) })
  const wrapper = mount(Harness, { global: { plugins: [pinia] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, auth, spaces, target, kinship: useKinshipStore(pinia), pfv: usePersonalFamilyViewStore(pinia) }
}

describe('KinshipTermPanel current viewer and optional preference controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    vi.mocked(kinshipApi.resolveKinship).mockImplementation(async (space, viewer, target) => resolution(space, viewer, target))
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValue(page([]))
    vi.mocked(pfvApi.fetchPersonalFamilyView).mockResolvedValue(null)
  })

  it('queries this target before pagination and only offers server-authorized effective actions', async () => {
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValue(page([
      suggestion(),
      suggestion({ id: 71, state: 'expired' }),
      suggestion({ id: 72, value: { term: '外婆' }, allowed_actions: ['open_details'] }),
      suggestion({ id: 73, value: { term: '外婆', can_restore: false } }),
    ]))
    const { wrapper } = await mountPanel()
    expect(suggestionsApi.fetchSuggestions).toHaveBeenCalledWith(7, null, 50, 'term_preference', 2)
    const rows = wrapper.findAll('[data-test="kinship-suggestion"]')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.find('[data-test="kinship-keep-btn"]').exists()).toBe(true)
    expect(rows[0]!.find('[data-test="kinship-restore-btn"]').exists()).toBe(true)
    expect(rows[1]!.findAll('button')).toHaveLength(0)
    expect(rows[2]!.find('[data-test="kinship-restore-btn"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(['target', 'space', 'account'] as const)('%s changes reset immediately and discard the previous response', async (change) => {
    const pending = deferred<SuggestionsPage>()
    vi.mocked(suggestionsApi.fetchSuggestions).mockReturnValueOnce(pending.promise)
    const { wrapper, auth, spaces, target } = await mountPanel()
    if (change === 'target') target.value = 3
    else if (change === 'space') spaces.currentSpaceId = 8
    else auth.user = { ...auth.user!, id: 4 }
    await nextTick()
    expect(wrapper.find('[data-test="kinship-suggestion"]').exists()).toBe(false)
    await flushPromises()
    pending.resolve(page([suggestion({ value: { term: '旧账号旧人物的叫法' } })]))
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧账号旧人物的叫法')
    expect(kinshipApi.resolveKinship).toHaveBeenLastCalledWith(change === 'space' ? 8 : 7, change === 'account' ? 4 : 1, change === 'target' ? 3 : 2)
    expect(suggestionsApi.fetchSuggestions).toHaveBeenLastCalledWith(change === 'space' ? 8 : 7, null, 50, 'term_preference', change === 'target' ? 3 : 2)
    wrapper.unmount()
  })

  it('clears the previous target controls while the next target is still loading', async () => {
    const next = deferred<SuggestionsPage>()
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce(page([suggestion()])).mockReturnValueOnce(next.promise)
    const { wrapper, target } = await mountPanel()
    expect(wrapper.find('[data-test="kinship-keep-btn"]').exists()).toBe(true)
    target.value = 3
    await nextTick()
    expect(wrapper.find('[data-test="kinship-suggestion"]').exists()).toBe(false)
    next.resolve(page([]))
    await flushPromises()
    wrapper.unmount()
  })

  it('keep invalidates all personal term and PFV spaces and reloads the active view', async () => {
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce(page([suggestion()]))
    vi.mocked(suggestionsApi.submitSuggestion).mockResolvedValue(kept())
    const { wrapper, kinship, pfv } = await mountPanel()
    kinship.resolveCache.set('8:1:3', resolution(8, 1, 3))
    pfv.bySpace.set(7, snapshot(7))
    pfv.bySpace.set(8, snapshot(8))
    await wrapper.find('[data-test="kinship-keep-btn"]').trigger('click')
    await flushPromises()
    expect(suggestionsApi.submitSuggestion).toHaveBeenCalledWith(7, 70, {
      expected_revision: 2, evidence_hash: 'evidence', confirm: true,
    }, expect.any(String))
    expect(kinship.cachedResolve(8, 1, 3)).toBeNull()
    expect(pfv.forSpace(8)).toBeNull()
    expect(pfvApi.fetchPersonalFamilyView).toHaveBeenCalledWith(7, null)
    expect(wrapper.find('[data-test="kinship-keep-btn"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('restore uses current projection metadata and leaves other spaces cached', async () => {
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce(page([suggestion()]))
    vi.mocked(suggestionsApi.restoreSuggestionTerm).mockResolvedValue()
    const { wrapper, kinship, pfv } = await mountPanel()
    kinship.resolveCache.set('8:1:3', resolution(8, 1, 3))
    pfv.bySpace.set(8, snapshot(8))
    await wrapper.find('[data-test="kinship-restore-btn"]').trigger('click')
    await flushPromises()
    expect(suggestionsApi.restoreSuggestionTerm).toHaveBeenCalledWith(7, 70, {
      expected_revision: 2, expected_projection_revision: 3, semantic_hash: 'current-hash',
    }, expect.any(String))
    expect(kinship.cachedResolve(8, 1, 3)).not.toBeNull()
    expect(pfv.forSpace(8)).not.toBeNull()
    expect(pfvApi.fetchPersonalFamilyView).toHaveBeenCalledWith(7, null)
    expect(wrapper.find('[data-test="kinship-restore-btn"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('late keep result cannot clear another account caches or show a success message', async () => {
    const pending = deferred<SuggestionSubmitPreferenceResult>()
    vi.mocked(suggestionsApi.fetchSuggestions).mockResolvedValueOnce(page([suggestion()]))
    vi.mocked(suggestionsApi.submitSuggestion).mockReturnValueOnce(pending.promise)
    const { wrapper, auth, kinship, pfv } = await mountPanel()
    await wrapper.find('[data-test="kinship-keep-btn"]').trigger('click')
    auth.user = { ...auth.user!, id: 4 }
    await flushPromises()
    pfv.bySpace.set(7, snapshot(7))
    pending.resolve(kept())
    await flushPromises()
    expect(kinship.cachedResolve(7, 4, 2)).not.toBeNull()
    expect(pfv.forSpace(7)).not.toBeNull()
    expect(pfvApi.fetchPersonalFamilyView).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('已保存为你的叫法')
    wrapper.unmount()
  })
})
