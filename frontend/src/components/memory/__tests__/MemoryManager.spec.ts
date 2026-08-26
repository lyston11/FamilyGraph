import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ElementPlus from 'element-plus'

import * as memoryApi from '@/api/memory'
import CitationList from '@/components/memory/CitationList.vue'
import MemoryManager from '@/components/memory/MemoryManager.vue'
import { useSpacesStore } from '@/stores/spaces'
import type { Memory, MemoryCandidate } from '@/types/memory'

vi.mock('@/api/memory', () => ({
  fetchMemoryCandidates: vi.fn(),
  fetchMemories: vi.fn(),
  confirmMemoryCandidate: vi.fn(),
  dismissMemoryCandidate: vi.fn(),
  revokeMemory: vi.fn(),
  deleteMemory: vi.fn(),
  searchMemory: vi.fn().mockResolvedValue([]),
  friendlyMemoryError: vi.fn((_code: string, fallback?: string) => fallback ?? '操作失败'),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
}))

const mockedCandidates = vi.mocked(memoryApi.fetchMemoryCandidates)
const mockedMemories = vi.mocked(memoryApi.fetchMemories)
const mockedConfirm = vi.mocked(memoryApi.confirmMemoryCandidate)
const mockedSearch = vi.mocked(memoryApi.searchMemory)

const candidate: MemoryCandidate = {
  id: 1,
  source_message_id: 9,
  source_document_ref: null,
  source_span_json: { start: 0, end: 2 },
  raw_quote: '每年春节一起包饺子。',
  summary: '春节包饺子',
  suggested_scope: 'household',
  purpose: '家庭活动提醒',
  sensitivity: 'normal',
  extractor_version: 'candidate-v1',
  status: 'pending',
  memory_id: null,
  created_at: '2026-08-26T00:00:00',
  decided_at: null,
}

const savedMemory: Memory = {
  id: 3,
  source_candidate_id: 1,
  source_message_id: 9,
  source_document_ref: null,
  raw_quote: candidate.raw_quote,
  content: candidate.summary,
  purpose: candidate.purpose,
  scope: 'household',
  space_id: 5,
  sensitivity: 'normal',
  confirmation_status: 'confirmed',
  revision: 1,
  retention_until: null,
  status: 'active',
  revoked_at: null,
  created_at: '2026-08-26T00:00:00',
  updated_at: '2026-08-26T00:00:00',
}

async function mountManager(): Promise<ReturnType<typeof mount>> {
  const pinia = createPinia()
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [{
    id: 5,
    name: '我家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-26T00:00:00',
    pending_count: 0,
    member_count: 2,
  }]
  spaces.currentSpaceId = 5
  const wrapper = mount(MemoryManager, {
    global: { plugins: [pinia, ElementPlus] },
  })
  await vi.waitFor(() => expect(mockedCandidates).toHaveBeenCalled())
  return wrapper
}

describe('MemoryManager（V2.5）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedCandidates.mockResolvedValue([candidate])
    mockedMemories.mockResolvedValue([savedMemory])
    mockedConfirm.mockResolvedValue(savedMemory)
    mockedSearch.mockResolvedValue([])
  })

  it('展示候选的原话、摘要、敏感等级，并在确认前不执行记忆写入', async () => {
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('每年春节一起包饺子。')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('春节包饺子')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('普通')
    expect(mockedConfirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('确认时提交用户明确选择的 scope 和保留期限', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await wrapper.find('[data-test="memory-retention-days"] input').setValue('30')
    await wrapper.find('[data-test="confirm-memory-submit"]').trigger('click')

    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith(1, {
      scope: 'private',
      retention_days: 30,
    }))
    wrapper.unmount()
  })

  it('空间 RAG 检索只在输入后请求当前空间，并展示无结果空态', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="rag-search-input"]').setValue('春节')
    await new Promise((resolve) => setTimeout(resolve, 300))

    expect(mockedSearch).toHaveBeenCalledWith(5, '春节')
    expect(wrapper.find('[data-test="rag-empty"]').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('CitationList（来源可追溯投影）', () => {
  it('显示来源类型、scope、修订和 citation handle，不把引用当成指令', () => {
    const wrapper = mount(CitationList, {
      props: {
        citations: [{
          source_type: 'memory',
          source_id: '3',
          text: '春节包饺子',
          scope: 'household:5',
          sensitivity: 'normal',
          revision: 1,
          citation_handle: 'rag:3:r1:c7',
        }],
      },
      global: { plugins: [ElementPlus] },
    })

    expect(wrapper.find('[data-test="citation-item"]').text()).toContain('确认记忆')
    expect(wrapper.text()).toContain('household:5')
    expect(wrapper.text()).toContain('rag:3:r1:c7')
  })
})
