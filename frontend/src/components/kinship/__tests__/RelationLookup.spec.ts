import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as kinshipApi from '@/api/kinship'
import { ApiError } from '@/api/errors'
import ElementPlus from 'element-plus'
import RelationLookup from '@/components/kinship/RelationLookup.vue'
import { useSpacesStore } from '@/stores/spaces'
import type { ParseResult } from '@/types/kinship'

/**
 * RelationLookup 四级渲染合同（V2.3 KI-3）：
 * - determined → 概念称谓 + 图上依据 + 词素 chips；
 * - supported → 确认提案卡片，明确「不会自动改动档案事实」；
 * - ambiguous → 内联追问，可再次输入；
 * - conflicting → 冲突列表 + 原文保留展示；
 * - found=false / flag 503 → 优雅降级（不伪造结论 / 隐藏入口）。
 */

vi.mock('@/api/kinship', () => ({
  KINSHIP_FLAG_DISABLED: 'KINSHIP_FLAG_DISABLED',
  fetchMyTerms: vi.fn(),
  updateMyTerm: vi.fn(),
  resolveKinship: vi.fn(),
  recordTermUsage: vi.fn(),
  parseRelationText: vi.fn(),
}))

const mockedParse = vi.mocked(kinshipApi.parseRelationText)

function makeParse(overrides: Partial<ParseResult> = {}): ParseResult {
  return {
    raw_text_id: 1,
    normalized_text: '舅爷爷',
    resolution_class: 'determined',
    candidate: {
      concept_code: 'E_GRANDPATERNAL_UNCLE',
      term: '舅爷爷',
      term_source_level: 'system',
    },
    graph_proof: { found: true, explanation_structural: '奶奶的兄弟' },
    proposals: [],
    conflicts: [],
    clarifying_question: null,
    evidence_morphemes: ['舅', '爷'],
    ...overrides,
  }
}

async function mountLookup(): Promise<ReturnType<typeof mount>> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const spaces = useSpacesStore()
  spaces.spaces.push({
    id: 10,
    name: '我的家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-26T00:00:00',
    pending_count: 0,
    member_count: 2,
  })
  spaces.currentSpaceId = 10
  const wrapper = mount(RelationLookup, { global: { plugins: [pinia, ElementPlus] } })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

async function submitQuery(wrapper: ReturnType<typeof mount>, text = '舅爷爷'): Promise<void> {
  await wrapper.find('input[data-test="lookup-input"]').setValue(text)
  await wrapper.find('[data-test="lookup-submit"]').trigger('click')
  await new Promise((resolve) => setTimeout(resolve))
}

describe('RelationLookup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('determined：概念称谓 + 图上依据 + 依据词素 chips', async () => {
    mockedParse.mockResolvedValue(makeParse())
    const wrapper = await mountLookup()

    expect(wrapper.find('[data-test="relation-lookup"]').exists()).toBe(true)
    await submitQuery(wrapper)

    expect(mockedParse).toHaveBeenCalledWith(10, '舅爷爷')
    const panel = wrapper.find('[data-test="lookup-determined"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('舅爷爷')
    expect(panel.text()).toContain('标准称谓')
    expect(panel.text()).toContain('图上依据：奶奶的兄弟')
    expect(wrapper.findAll('[data-test="lookup-morphemes"] .el-tag')).toHaveLength(2)
  })

  it('supported：提案卡片明确「不会自动改动档案事实」', async () => {
    mockedParse.mockResolvedValue(
      makeParse({
        resolution_class: 'supported',
        candidate: null,
        proposals: [{ kind: 'source_fact', fact_type: 'parent_child', summary: '「老妈」可能指你的母亲' }],
        graph_proof: { found: false, explanation_structural: null },
        evidence_morphemes: ['妈'],
      }),
    )
    const wrapper = await mountLookup()
    await submitQuery(wrapper, '老妈')

    const card = wrapper.find('[data-test="lookup-supported"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('「老妈」可能指你的母亲')
    expect(card.text()).toContain('不会自动改动任何档案事实')
    expect(wrapper.find('[data-test="lookup-determined"]').exists()).toBe(false)
  })

  it('ambiguous：内联展示 clarifying_question 并保留输入框再次输入', async () => {
    mockedParse.mockResolvedValue(
      makeParse({
        resolution_class: 'ambiguous',
        candidate: null,
        graph_proof: { found: false, explanation_structural: null },
        clarifying_question: '你说的「老太」是指爸爸那边还是妈妈那边的长辈？',
      }),
    )
    const wrapper = await mountLookup()
    await submitQuery(wrapper, '老太')

    expect(wrapper.find('[data-test="lookup-clarify"]').text()).toContain('爸爸那边还是妈妈那边')
    // 输入框仍在，可继续作答
    expect(wrapper.find('input[data-test="lookup-input"]').exists()).toBe(true)
  })

  it('conflicting：冲突列表 + 原文保留展示', async () => {
    mockedParse.mockResolvedValue(
      makeParse({
        resolution_class: 'conflicting',
        normalized_text: '爸爸',
        candidate: null,
        graph_proof: { found: true, explanation_structural: null },
        conflicts: ['图中 A 既是 B 的父亲又被记录为 B 的配偶'],
      }),
    )
    const wrapper = await mountLookup()
    await submitQuery(wrapper, '爸爸')

    const panel = wrapper.find('[data-test="lookup-conflicting"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('既是 B 的父亲又被记录为 B 的配偶')
    expect(wrapper.find('[data-test="lookup-original-text"]').text()).toContain('你输入的原文「爸爸」')
  })

  it('found=false：降级提示，不渲染任何概念结论', async () => {
    mockedParse.mockResolvedValue(
      makeParse({
        resolution_class: 'determined',
        graph_proof: { found: false, explanation_structural: null },
      }),
    )
    const wrapper = await mountLookup()
    await submitQuery(wrapper)

    expect(wrapper.find('[data-test="lookup-unproven"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="lookup-determined"]').exists()).toBe(false)
  })

  it('flag 关闭（503）：整个入口隐藏', async () => {
    mockedParse.mockRejectedValue(new ApiError(503, 'KINSHIP_FLAG_DISABLED', '关系智能能力未启用'))
    const wrapper = await mountLookup()
    await submitQuery(wrapper)

    expect(wrapper.find('[data-test="relation-lookup"]').exists()).toBe(false)
  })

  it('flag 正常但解析报错：入口保留并显示错误文案', async () => {
    mockedParse.mockRejectedValue(new ApiError(500, 'HTTP_ERROR', '请求失败（500）'))
    const wrapper = await mountLookup()
    await submitQuery(wrapper)

    expect(wrapper.find('[data-test="relation-lookup"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="lookup-error"]').text()).toContain('请求失败')
  })
})
