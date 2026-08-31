import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

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

// MemoryManager setup 期 useMessage/useDialog：需要 provider 祖先（App 层已备好）；
// n-modal 默认 teleport 到 body，确认弹层内的断言与点击走 document 查询
function clickDocument(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
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
  const Harness = defineComponent({
    render() {
      return h('div', [
        h(NMessageProvider, () => h(NDialogProvider, () => h(MemoryManager))),
      ])
    },
  })
  const wrapper = mount(Harness, {
    global: { plugins: [pinia] },
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
    await new Promise((resolve) => setTimeout(resolve))
    const retentionInput = document.querySelector(
      '[data-test="memory-retention-days"] input',
    ) as HTMLInputElement
    expect(retentionInput).not.toBeNull()
    retentionInput.value = '30'
    retentionInput.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))
    clickDocument('[data-test="confirm-memory-submit"]')

    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith(1, {
      scope: 'private',
      retention_days: 30,
    }))
    wrapper.unmount()
  })

  it('确认弹层完整展示原话、敏感等级与保留期限（V2.5 合同：确认前可见）', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    const dialog = document.querySelector('[data-test="confirm-memory-dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('敏感等级：普通')
    expect(dialog?.textContent).toContain('每年春节一起包饺子。')
    expect(dialog?.textContent).toContain('保存范围')
    expect(dialog?.textContent).toContain('保留期限')
    wrapper.unmount()
  })

  it('高敏感候选：共享 scope 选项置灰不可选，弹层保留可见降级文案', async () => {
    mockedCandidates.mockResolvedValue([{ ...candidate, id: 2, sensitivity: 'high' }])
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    // fail-closed 降级文案可见（type-safety.md / V2.5 合同）
    expect(document.querySelector('[data-test="memory-sharing-warning"]')).not.toBeNull()

    // 共享选项置灰：点击后选择不改变，确认仍提交 private（不做乐观放宽）
    ;(document.querySelector('.n-base-selection') as HTMLElement).dispatchEvent(
      new MouseEvent('click', { bubbles: true }),
    )
    await new Promise((resolve) => setTimeout(resolve))
    const sharedOption = [...document.querySelectorAll('.n-base-select-option')].find((el) =>
      el.textContent?.includes('家庭共享'),
    )
    expect(sharedOption).not.toBeUndefined()
    sharedOption!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))
    clickDocument('[data-test="confirm-memory-submit"]')

    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith(2, { scope: 'private' }))
    wrapper.unmount()
  })

  it('空间 RAG 检索只在输入后请求当前空间，并展示无结果空态', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="rag-search-input"] input').setValue('春节')
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
    })

    expect(wrapper.find('[data-test="citation-item"]').text()).toContain('确认记忆')
    expect(wrapper.text()).toContain('household:5')
    expect(wrapper.text()).toContain('rag:3:r1:c7')
  })
})
