import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import * as memoryApi from '@/api/memory'
import { ApiError } from '@/api/errors'
import CitationList from '@/components/memory/CitationList.vue'
import MemoryManager from '@/components/memory/MemoryManager.vue'
import { useMemoryStore } from '@/stores/memory'
import { useSpacesStore } from '@/stores/spaces'
import type { Memory, MemoryCandidate } from '@/types/memory'

vi.mock('@/api/memory', () => ({
  fetchPlatformFeatures: vi.fn(),
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

const mockedFeatureState = vi.mocked(memoryApi.fetchPlatformFeatures)
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
  source_kind: 'agent_message',
  source_status: 'available',
  allowed_scopes: ['private', 'household:5'],
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
  source_kind: 'agent_message',
  source_status: 'available',
  allowed_scopes: ['private', 'household:5'],
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
  await vi.waitFor(() => expect(mockedFeatureState).toHaveBeenCalled())
  await flushPromises()
  if (useMemoryStore(pinia).memoryEnabled) await vi.waitFor(() => expect(mockedCandidates).toHaveBeenCalled())
  return wrapper
}

/** 点击左侧分区导航按钮切换分区（按分区文字定位；09-06 起为设置页同构按钮导航） */
async function switchTab(wrapper: ReturnType<typeof mount>, label: string): Promise<void> {
  const tab = wrapper.findAll('.memory-tab').find((node) => node.text().includes(label))
  expect(tab, `tab ${label}`).not.toBeUndefined()
  await tab!.trigger('click')
  await new Promise((resolve) => setTimeout(resolve))
}

describe('MemoryManager（五分区，PRD §2.5 / design §5.4）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    mockedFeatureState.mockResolvedValue({ memory_enabled: true, rag_enabled: true })
    mockedCandidates.mockResolvedValue([candidate])
    mockedMemories.mockResolvedValue([savedMemory])
    mockedConfirm.mockResolvedValue(savedMemory)
    mockedCreateCandidate.mockResolvedValue(candidate)
    mockedSearch.mockResolvedValue([])
  })

  it('Memory 关闭时显示可行动说明并隐藏 Memory 写操作', async () => {
    mockedFeatureState.mockResolvedValue({ memory_enabled: false, rag_enabled: true })
    const wrapper = await mountManager()

    await vi.waitFor(() => expect(wrapper.find('[data-test="memory-disabled-state"]').exists()).toBe(true))
    expect(wrapper.find('[data-test="add-memory"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="confirm-candidate"]').exists()).toBe(false)
    expect(mockedCandidates).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('RAG 关闭时 Memory 可用但检索操作呈独立关闭态', async () => {
    mockedFeatureState.mockResolvedValue({ memory_enabled: true, rag_enabled: false })
    mockedCandidates.mockResolvedValue([])
    const wrapper = await mountManager()

    await vi.waitFor(() => expect(wrapper.find('[data-test="private-section"]').isVisible()).toBe(true))
    await switchTab(wrapper, '检索与引用')
    expect(wrapper.find('[data-test="rag-section-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="rag-panel"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('两个开关都关闭时没有写入或搜索操作', async () => {
    mockedFeatureState.mockResolvedValue({ memory_enabled: false, rag_enabled: false })
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="memory-disabled-state"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="rag-disabled-state"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="add-memory"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="rag-search-input"]').exists()).toBe(false)
    expect(mockedCandidates).not.toHaveBeenCalled()
    expect(mockedMemories).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('能力状态请求失败明确显示未知状态，不假装功能关闭', async () => {
    mockedFeatureState.mockRejectedValue(new ApiError(503, 'HTTP_ERROR', '服务暂不可用'))
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="memory-feature-state-error"]').text()).toContain('暂时无法确认')
    expect(wrapper.find('[data-test="memory-disabled-state"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="rag-disabled-state"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="candidate-section"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="rag-section"]').exists()).toBe(false)
    expect(mockedCandidates).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('已启用后的状态刷新失败也会收起旧写入口', async () => {
    const wrapper = await mountManager()
    expect(wrapper.find('[data-test="confirm-candidate"]').exists()).toBe(true)
    mockedFeatureState.mockRejectedValue(new ApiError(503, 'HTTP_ERROR', '刷新失败'))
    await useMemoryStore(pinia).loadFeatureState().catch(() => undefined)
    await flushPromises()

    expect(wrapper.find('[data-test="memory-feature-state-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="confirm-candidate"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="add-memory"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="rag-disabled-state"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('旧页面卸载后的能力响应不会用新会话继续加载记忆', async () => {
    let resolveFeatures!: (features: { memory_enabled: boolean; rag_enabled: boolean }) => void
    mockedFeatureState.mockReturnValueOnce(new Promise((resolve) => { resolveFeatures = resolve }))
    const wrapper = await mountManager()
    wrapper.unmount()
    const memory = useMemoryStore(pinia)
    memory.clear()
    // A newly authenticated page may have already loaded its feature flags.
    memory.features = { memory_enabled: true, rag_enabled: true }
    resolveFeatures({ memory_enabled: true, rag_enabled: true })
    await flushPromises()

    expect(mockedCandidates).not.toHaveBeenCalled()
    expect(mockedMemories).not.toHaveBeenCalled()
    expect(memory.candidates).toEqual([])
  })

  it.each(['unavailable', 'unverified'] as const)('来源 %s 的候选和记忆只显示状态，不显示旧正文', async (sourceStatus) => {
    mockedCandidates.mockResolvedValue([{ ...candidate, source_status: sourceStatus, allowed_scopes: [] }])
    mockedMemories.mockResolvedValue([{
      ...savedMemory, scope: 'private', space_id: null, source_status: sourceStatus,
      allowed_scopes: [], content: '应被遮蔽的记忆摘要', raw_quote: '应被遮蔽的原文', purpose: '应被遮蔽的用途',
    }])
    const wrapper = await mountManager()

    expect(wrapper.find('[data-test="candidate-source-status"]').text()).toContain('内容暂不显示')
    expect(wrapper.find('[data-test="candidate-card"]').text()).not.toContain('春节包饺子')
    expect(wrapper.find('[data-test="confirm-candidate"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    expect(document.querySelector('[data-test="confirm-memory-dialog"]')).toBeNull()

    await switchTab(wrapper, '我的私有记忆')
    expect(wrapper.find('[data-test="memory-source-status"]').text()).toContain('内容暂不显示')
    expect(wrapper.find('[data-test="memory-card"]').text()).not.toContain('应被遮蔽')
    // 本人仍可管理不可用条目的生命周期。
    expect(wrapper.find('[data-test="delete-memory"]').exists()).toBe(true)
    expect(mockedConfirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('来源状态更新时，已打开的确认弹层同步隐藏正文并禁止确认', async () => {
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="confirm-memory-dialog"]')?.textContent).toContain('春节包饺子')

    useMemoryStore(pinia).candidates = [{
      ...candidate, source_status: 'unavailable', allowed_scopes: [],
      raw_quote: null, summary: null, purpose: null,
    }]
    await flushPromises()

    expect(document.querySelector('[data-test="confirm-memory-dialog"]')?.textContent).not.toContain('春节包饺子')
    expect(document.querySelector('[data-test="confirm-memory-source-status"]')?.textContent).toContain('来源不可用')
    expect((document.querySelector('[data-test="confirm-memory-submit"]') as HTMLButtonElement).disabled).toBe(true)
    clickDocument('[data-test="confirm-memory-submit"]')
    expect(mockedConfirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('确认 API 拒绝时保留弹层及候选，并显示真实错误', async () => {
    mockedConfirm.mockRejectedValue(new ApiError(409, 'MEMORY_STATE_CONFLICT', '这条候选已经由另一请求处理'))
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await flushPromises()
    clickDocument('[data-test="confirm-memory-submit"]')

    await vi.waitFor(() => expect(document.querySelector('[data-test="confirm-memory-error"]')?.textContent).toContain('另一请求处理'))
    expect(useMemoryStore(pinia).pendingCandidates).toHaveLength(1)
    expect(document.querySelector('[data-test="confirm-memory-dialog"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('RAG 候选不能共享到 allowed_scopes 之外的当前空间', async () => {
    mockedCandidates.mockResolvedValue([{ ...candidate, source_kind: 'rag_chunk', allowed_scopes: ['private'] }])
    const wrapper = await mountManager()
    await wrapper.find('[data-test="confirm-candidate"]').trigger('click')
    await flushPromises()
    expect(document.querySelector('[data-test="memory-privacy-impact"]')?.textContent).toContain('仍受原来源权限约束')

    ;(document.querySelector('.n-base-selection') as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    const sharedOption = [...document.querySelectorAll('.n-base-select-option')].find((el) => el.textContent?.includes('家庭共享'))
    expect(sharedOption?.classList.contains('n-base-select-option--disabled')).toBe(true)
    clickDocument('[data-test="confirm-memory-submit"]')
    await vi.waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith(1, { scope: 'private' }))
    wrapper.unmount()
  })

  it('五分区渲染；有待确认候选时默认进入待确认（候选视觉状态 = icon+文字）', async () => {
    const wrapper = await mountManager()

    const labels = wrapper.findAll('.memory-tab').map((node) => node.text())
    expect(labels.some((text) => text.includes('待确认'))).toBe(true)
    expect(labels).toEqual([
      expect.stringContaining('待确认'),
      expect.stringContaining('我的私有记忆'),
      expect.stringContaining('当前家庭共享'),
      expect.stringContaining('当前家族共享'),
      expect.stringContaining('检索与引用'),
    ])

    // 默认待确认：分区导航高亮 + 候选卡可见，且为「候选 · 未进入检索」icon+文字徽章（非仅颜色）
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="memory-tab-pending"]').classes()).toContain('is-active')
    })
    expect(wrapper.find('[data-test="candidate-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="candidate-state-badge"]').text()).toContain('候选 · 未进入检索')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('每年春节一起包饺子。')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('春节包饺子')
    expect(wrapper.find('[data-test="candidate-card"]').text()).toContain('敏感等级：普通')
    wrapper.unmount()
  })

  it('无待确认候选时默认进入「我的私有记忆」分区', async () => {
    mockedCandidates.mockResolvedValue([])
    const wrapper = await mountManager()

    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="private-section"]').isVisible()).toBe(true)
    })
    // 分区常驻 DOM（v-show），非当前分区仅隐藏
    expect(wrapper.find('[data-test="candidate-section"]').isVisible()).toBe(false)
    // 私有分区提供「新增记忆」入口（提交只能新建候选）
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
    mockedFeatureState.mockResolvedValue({ memory_enabled: true, rag_enabled: true })
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
        space_id: 5,
        allowed_scopes: ['private', 'household:5'],
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
    expect((document.querySelector('[data-test="memory-editor-quote"] textarea') as HTMLTextAreaElement).readOnly).toBe(true)

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
          source: {
            kind: 'rag_chunk', document_id: 2, chunk_id: 1,
            revision: 1, index_version: 'v1', space_id: 5,
          },
          idempotency_key: expect.any(String),
          suggested_scope: 'private',
          sensitivity: 'normal',
        }),
      ),
    )
    expect(mockedCreateCandidate.mock.calls[0]?.[0]).not.toHaveProperty('raw_quote')
    // 检索结果保存绝不直接写记忆（无绕过审计的直接发布）
    expect(mockedConfirm).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('Memory 关闭、RAG 开启时仍可检索，但结果无保存动作', async () => {
    mockedFeatureState.mockResolvedValue({ memory_enabled: false, rag_enabled: true })
    const wrapper = await mountManager()
    await wrapper.find('[data-test="rag-search-input"] input').setValue('春节')
    await vi.waitFor(() => expect(wrapper.find('[data-test="rag-result"]').exists()).toBe(true))

    expect(mockedSearch).toHaveBeenCalledWith(5, '春节')
    expect(wrapper.find('[data-test="rag-save-disabled-state"]').text()).toContain('暂时不能保存')
    expect(wrapper.find('[data-test="rag-save-candidate"]').exists()).toBe(false)
    expect(mockedCreateCandidate).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('检索结果缺少有效片段定位时禁止保存，不退化成手工来源', async () => {
    mockedSearch.mockResolvedValue([{
      chunk_id: 0, document_id: 2, source_type: 'memory', source_id: '3',
      text: '可读结果', scope: 'private', sensitivity: 'normal', revision: 1,
      index_version: '', space_id: null, allowed_scopes: ['private'], citation_handle: 'rag:invalid',
    }])
    const wrapper = await mountManager()
    await switchTab(wrapper, '检索与引用')
    await wrapper.find('[data-test="rag-search-input"] input').setValue('可读')
    await vi.waitFor(() => expect(wrapper.find('[data-test="rag-result"]').exists()).toBe(true))

    expect(wrapper.find('[data-test="rag-save-candidate"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="rag-source-incomplete"]').text()).toContain('重新检索')
    await wrapper.find('[data-test="rag-save-candidate"]').trigger('click')
    expect(document.querySelector('[data-test="memory-editor-dialog"]')).toBeNull()
    expect(mockedCreateCandidate).not.toHaveBeenCalled()
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
