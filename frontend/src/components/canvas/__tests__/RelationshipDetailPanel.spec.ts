import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RelationshipDetailPanel from '@/components/canvas/RelationshipDetailPanel.vue'
import type { PersonalFamilyViewEdge, PersonalFamilyViewPathStep } from '@/types/api'

/**
 * 只读关系说明面板（design.md §5.3）：
 * - 称谓、主路径、最多 3 条替代路径（超出截断并提示）、path_class/concept_code
 *   安全来源摘要、事实状态与更新时间；
 * - 遮罩/缺失安全降级；面板纯只读——「申请更正/查看待办」只 emit 安全跳转事件
 *   （页面导航到 /notifications），不产生任何 SourceFact 写操作。
 */

function makeStep(from: number, to: number, edgeType = 'parent'): PersonalFamilyViewPathStep {
  return { from, to, edge_type: edgeType, subtype: null, direction: 'up', fact_id: from * 100 + to }
}

function makeEdge(overrides: Partial<PersonalFamilyViewEdge> = {}): PersonalFamilyViewEdge {
  return {
    from_user_id: 1,
    to_user_id: 4,
    edge_kind: 'sibling',
    path: [makeStep(1, 2), makeStep(2, 4, 'sibling')],
    alternative_paths: [],
    path_class: 'collateral',
    concept_code: 'SIBLING_BROTHER',
    term: '堂弟',
    inclusion_reason_code: 'confirmed_path',
    ...overrides,
  }
}

const NAMES = new Map([
  [1, '张三'],
  [2, '祖父'],
  [3, '父亲'],
  [4, '堂弟'],
])

function resolveName(userId: number): string | null {
  return NAMES.get(userId) ?? null
}

function mountPanel(edge: PersonalFamilyViewEdge) {
  return mount(RelationshipDetailPanel, {
    props: {
      edge,
      viewVersion: 5,
      computedAt: '2026-09-01T08:00:00',
      resolveName,
    },
  })
}

describe('RelationshipDetailPanel 渲染', () => {
  it('渲染称谓、关系类型摘要、concept_code、主路径链与版本时间', () => {
    const wrapper = mountPanel(makeEdge())
    expect(wrapper.find('[data-test="relation-term"]').text()).toContain('堂弟')
    expect(wrapper.find('[data-test="relation-path-class"]').text()).toContain('旁系血亲')
    expect(wrapper.find('[data-test="relation-concept-code"]').text()).toContain('SIBLING_BROTHER')
    expect(wrapper.find('[data-test="relation-main-path-text"]').text()).toContain('张三 → 祖父 → 堂弟')
    expect(wrapper.find('[data-test="relation-meta"]').text()).toContain('v5')
    expect(wrapper.find('[data-test="relation-meta"]').text()).toContain('2026-09-01T08:00:00')
  })

  it('替代路径最多渲染 3 条（服务端返回更多时截断并提示）', () => {
    const edge = makeEdge({
      alternative_paths: [
        [makeStep(1, 3), makeStep(3, 4)],
        [makeStep(1, 2), makeStep(2, 4, 'sibling')],
        [makeStep(1, 3, 'spouse'), makeStep(3, 4)],
        [makeStep(1, 2), makeStep(2, 3), makeStep(3, 4)],
      ],
    })
    const wrapper = mountPanel(edge)
    const rendered = wrapper.findAll('[data-test^="relation-alt-path-"]')
    expect(rendered).toHaveLength(3)
    // 超出截断并提示（PRD §2.4 合同）
    expect(wrapper.find('[data-test="relation-alt-paths-truncated"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="relation-alt-paths-truncated"]').text()).toContain('前 3 条')
  })

  it('替代路径不超过 3 条时不显示截断提示', () => {
    const edge = makeEdge({
      alternative_paths: [[makeStep(1, 3), makeStep(3, 4)]],
    })
    const wrapper = mountPanel(edge)
    expect(wrapper.find('[data-test="relation-alt-paths-truncated"]').exists()).toBe(false)
  })

  it('展示事实状态（inclusion_reason_code 文字化，未知码回退安全文案）', () => {
    const wrapper = mountPanel(makeEdge({ inclusion_reason_code: 'confirmed_path' }))
    expect(wrapper.find('[data-test="relation-fact-state"]').text()).toContain('已确认的关系事实')

    const unknown = mountPanel(makeEdge({ inclusion_reason_code: 'mystery_code' }))
    expect(unknown.find('[data-test="relation-fact-state"]').text()).toContain(
      '服务端确认的关系事实',
    )
  })

  it('无替代路径时显示安全降级文案', () => {
    const wrapper = mountPanel(makeEdge({ alternative_paths: [] }))
    expect(wrapper.find('[data-test="relation-alt-paths"]').text()).toContain('暂无替代路径')
  })
})

describe('RelationshipDetailPanel 安全降级', () => {
  it('称谓缺失显示「暂无称谓」；concept_code 缺失不渲染编码行', () => {
    const wrapper = mountPanel(makeEdge({ term: null, concept_code: null }))
    expect(wrapper.find('[data-test="relation-term"]').text()).toContain('暂无称谓')
    expect(wrapper.find('[data-test="relation-concept-code"]').exists()).toBe(false)
  })

  it('路径为空显示「暂无路径说明」', () => {
    const wrapper = mountPanel(makeEdge({ path: [] }))
    expect(wrapper.find('[data-test="relation-main-path"]').text()).toContain('暂无路径说明')
  })

  it('路径成员不在快照中时用安全占位，不显示原始 id', () => {
    const wrapper = mountPanel(
      makeEdge({ path: [makeStep(99, 2), makeStep(2, 4, 'sibling')] }),
    )
    const text = wrapper.find('[data-test="relation-main-path-text"]').text()
    expect(text).toContain('某位成员')
    expect(text).not.toContain('99')
  })

  it('computedAt 缺失显示「暂无更新时间」', () => {
    const wrapper = mount(RelationshipDetailPanel, {
      props: {
        edge: makeEdge(),
        viewVersion: 5,
        computedAt: null,
        resolveName,
      },
    })
    expect(wrapper.find('[data-test="relation-meta"]').text()).toContain('暂无更新时间')
  })
})

describe('RelationshipDetailPanel 只读边界', () => {
  it('申请更正/查看待办为安全跳转事件：仅 emit，无任何写入或 Bridge 操作控件', async () => {
    const wrapper = mountPanel(makeEdge())
    const correct = wrapper.find('[data-test="relation-correct"]')
    const todo = wrapper.find('[data-test="relation-view-todos"]')
    // 按钮已接线（Phase 4）：emit 安全跳转事件，由页面导航到 /notifications
    expect(correct.attributes('disabled')).toBeUndefined()
    expect(todo.attributes('disabled')).toBeUndefined()

    await correct.trigger('click')
    await todo.trigger('click')
    expect(wrapper.emitted('request-correction')).toEqual([[]])
    expect(wrapper.emitted('view-todos')).toEqual([[]])
    // 面板不渲染任何 Bridge approve/reject/consent/revoke 控件（PRD §2.4/§2.6）
    expect(wrapper.text()).not.toContain('同意')
    expect(wrapper.text()).not.toContain('拒绝')
    expect(wrapper.text()).not.toContain('撤销')

    await wrapper.find('[data-test="relation-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
  })
})
