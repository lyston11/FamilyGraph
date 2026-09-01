import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import * as memoryApi from '@/api/memory'
import CitationList from '@/components/memory/CitationList.vue'
import MemoryManager from '@/components/memory/MemoryManager.vue'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import type { Memory, MemoryCandidate } from '@/types/memory'

vi.mock('@/api/memory', () => ({
  fetchMemoryCandidates: vi.fn(),
  fetchMemories: vi.fn(),
  confirmMemoryCandidate: vi.fn(),
  createMemoryCandidate: vi.fn(),
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
const mockedCreateCandidate = vi.mocked(memoryApi.createMemoryCandidate)
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
// 弹层（n-modal）默认 teleport 到 body，确认弹层内的断言与点击走 document 查询
function clickDocument(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

let pinia: Pinia

async function mountManager(): Promise<ReturnType<typeof mount>> {
  pinia = createPinia()
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
    attachTo: document.body,
  })
  await vi.waitFor(() => expect(mockedCandidates).toHaveBeenCalled())
  return wrapper
}

/** 点击 n-tabs 标签头切换面板（按标签文字定位） */
async function switchTab(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const tab = wrapper.findAll('.n-tabs-tab').find((node) => node.text().includes(label))
  expect(tab, `tab ${label}`).not.toBeUndefined()
  await tab!.trigger('click')
  await new Promise((resolve) => setTimeout(resolve))
}

describe('MemoryManager（五标签，PRD §2.5 / design §5.4）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    mockedCandidates.mockResolvedValue([candidate])
    mockedMemories.mockResolvedValue([savedMemory])
    mockedConfirm.mockResolvedValue(savedMemory)
    mockedCreateCandidate.mockResolvedValue(candidate)
    mockedSearch.mockResolvedValue([])
  })

  it('五标签渲染；有待确认候选时默认进入待确认（候选视觉状态 = icon+文字）', async () => {
    const wrapper = await mountManager()

    const labels = wrapper.findAll('.n-tabs-tab').map((node) => node.text())
    expect(labels.some((text) => text.includes('待确认'))).toBe(true)
    expect(labels).toEqual([
      expect.stringContaining('待确认'),
      expect.stringContaining('我的私有记忆'),
      expect.stringContaining('当前家庭共享'),
      expect.stringContaining('当前家族共享'),
      expect.stringContaining('检索与引用'),
    ])

    // 默认待确认：候选卡可见，且为「候选 · 未进入检索」icon+文字徽章（非仅颜色）
    expect(wrapper.find('[data-test="candidate-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="candidate-state-badge"]').text()).toContain('候选 · 未进入检索')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('每年春节一起包饺子。')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('春节包饺子')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('敏感等级：普通')
    wrapper.unmount()
  })

  it('无待确认候选时默认进入「我的私有记忆」标签', async () => {
    mockedCandidates.mockResolvedValue([])
    const wrapper = await mountManager()

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="private-section"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="candidate-section"]').exists()).toBe(false)
    // 私有标签提供「新增记忆」入口（提交只能新建候选）
    expect(wrapper.find('[data-test="add-memory"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('候选确认后重新拉取服务端候选与记忆列表（无乐观副本）', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    const candidateCallsBefore = mockedCandidates.mock.calls.length
    const memoryCallsBefore = mockedMemories.mock.calls.length
    clickDocument('[data-test="confirm-memory-submit"]')

    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalled())
    await vi.waitFor(() =>
      expect(mockedCandidates.mock.calls.length).toBeGreaterThan(candidateCallsBefore),
    )
    await vi.waitFor(() => expect(mockedMemories.mock.calls.length).toBeGreaterThan(memoryCallsBefore))
    wrapper.unmount()
  })

  it('确认弹层展示原话/摘要/用途/敏感等级/隐私影响；默认 scope=private', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    const dialog = document.querySelector('[data-test="confirm-memory-dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog?.textContent).toContain('敏感等级：普通')
    expect(dialog?.textContent).toContain('每年春节一起包饺子。')
    expect(dialog?.textContent).toContain('用途：家庭活动提醒')
    expect(dialog?.textContent).toContain('仅本人可见')
    // 隐私影响说明确认前可见
    expect(document.querySelector('[data-test="memory-privacy-impact"]')?.textContent).toContain('仅本人可见')
    // 稍后处理入口存在（只能确认/拒绝/稍后处理）
    expect(document.querySelector('[data-test="confirm-memory-later"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('private 确认提交默认 scope 与保留期限；共享 scope 选项可见', async () => {
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

  it('共享确认交互：选择家庭共享 scope 后提交携带 household:<space_id>', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    // 打开 scope 下拉并选择家庭共享（明确确认共享目标）
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
    // 共享时隐私影响说明更新为家庭共享文案
    expect(document.querySelector('[data-test="memory-privacy-impact"]')?.textContent).toContain('家庭空间的授权成员')
    clickDocument('[data-test="confirm-memory-submit"]')

    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith(1, { scope: 'household:5' }))
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

  it('空间切换：组件清理旧空间共享记忆/RAG 分区并重新拉取新空间', async () => {
    const wrapper = await mountManager()
    const memoryStore = useMemoryStore(pinia)
    const resetSpy = vi.spyOn(memoryStore, 'resetForSpace')
    const spacesStore = useSpacesStore(pinia)
    mockedMemories.mockClear()

    spacesStore.spaces.push({
      id: 9,
      name: '张氏家族',
      owner_id: 2,
      kind: 'lineage',
      created_at: '2026-08-26T00:00:00',
      pending_count: 0,
      member_count: 3,
    })
    spacesStore.currentSpaceId = 9
    await new Promise((resolve) => setTimeout(resolve))

    expect(resetSpy).toHaveBeenCalledWith(5)
    await vi.waitFor(() => expect(mockedMemories).toHaveBeenCalledWith(9))
    wrapper.unmount()
  })
})

describe('MemoryManager 检索与引用（只读 + 保存只能新建候选）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    mockedCandidates.mockResolvedValue([])
    mockedMemories.mockResolvedValue([])
    mockedConfirm.mockResolvedValue(savedMemory)
    mockedCreateCandidate.mockResolvedValue(candidate)
    mockedSearch.mockResolvedValue([
      {
        chunk_id: 1,
        document_id: 2,
        source_type: 'memory',
        source_id: '3',
        text: '每年春节一起包饺子。',
        scope: 'household:5',
        sensitivity: 'normal',
        revision: 1,
        index_version: 'v1',
        citation_handle: 'rag:3:r1:c7',
      },
    ])
  })

  it('检索结果只读展示 citation_handle/source/scope/revision；「保存」只新建候选', async () => {
    const wrapper = await mountManager()
    await switchTab(wrapper, '检索与引用')

    await wrapper.find('[data-test="rag-search-input"] input').setValue('春节')
    await new Promise((resolve) => setTimeout(resolve, 300))
    expect(mockedSearch).toHaveBeenCalledWith(5, '春节')

    const result = wrapper.find('[data-test="rag-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('household:5')
    expect(result.text()).toContain('rag:3:r1:c7')
    expect(result.text()).toContain('memory · 来源 3 · 修订 1')

    // 「保存」打开共用编辑器（预填原文），提交走 POST /memory-candidates
    await wrapper.find('[data-test="rag-save-candidate"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    const dialog = document.querySelector('[data-test="memory-editor-dialog"]')
    expect(dialog).not.toBeNull()

    const purposeInput = document.querySelector(
      '[data-test="memory-editor-purpose"] input',
    ) as HTMLInputElement
    purposeInput.value = '家庭传统记录'
    purposeInput.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve))

    clickDocument('[data-test="memory-editor-submit"]')
    await vi.waitFor(() =>
      expect(mockedCreateCandidate).toHaveBeenCalledWith(
        expect.objectContaining({
          raw_quote: '每年春节一起包饺子。',
          suggested_scope: 'private',
          sensitivity: 'normal',
        }),
      ),
    )
    // 检索结果保存绝不直接写记忆（无绕过审计的直接发布）
    expect(mockedConfirm).not.toHaveBeenCalled()
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
