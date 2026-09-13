import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StructuralRelationshipPanel from '@/components/canvas/StructuralRelationshipPanel.vue'
import type { PersonalFamilyViewTopologyEdge } from '@/types/api'

/**
 * 结构边说明面板（09-13 design.md §5.3）：纯展示——只描述两个实际端点之间的
 * 直接事实类型与已确认状态，不使用 viewer 视角称谓，不伪造个人路径证据；
 * 关闭/待办为只读安全出口，无任何 Bridge 操作控件。
 */

function makeEdge(overrides: Partial<PersonalFamilyViewTopologyEdge> = {}): PersonalFamilyViewTopologyEdge {
  return {
    id: 'parent:biological:3:1',
    from_user_id: 3,
    to_user_id: 1,
    edge_kind: 'parent',
    subtype: 'biological',
    ...overrides,
  }
}

function mountPanel(edge: PersonalFamilyViewTopologyEdge, resolveName: (id: number) => string | null) {
  return mount(StructuralRelationshipPanel, {
    props: { edge, viewVersion: 5, computedAt: '2026-09-13T10:00:00', resolveName },
  })
}

describe('StructuralRelationshipPanel 渲染', () => {
  it('展示两端成员名字、关系类型与子类型', () => {
    const wrapper = mountPanel(makeEdge(), (id) => (id === 3 ? '父亲' : '本人'))
    expect(wrapper.find('[data-test="structural-endpoints"]').text()).toContain('父亲')
    expect(wrapper.find('[data-test="structural-endpoints"]').text()).toContain('本人')
    expect(wrapper.find('[data-test="structural-kind"]').text()).toContain('亲子·亲生')
  })

  it('对称关系（配偶）无子类型标注', () => {
    const wrapper = mountPanel(makeEdge({ id: 'spouse:-:1:2', from_user_id: 1, to_user_id: 2, edge_kind: 'spouse', subtype: null }), (id) => `成员${id}`)
    expect(wrapper.find('[data-test="structural-kind"]').text()).toContain('配偶')
    expect(wrapper.find('[data-test="structural-subtype"]').exists()).toBe(false)
  })

  it('端点名不可解析时安全占位（不显示原始 id）', () => {
    const wrapper = mountPanel(makeEdge(), () => null)
    expect(wrapper.find('[data-test="structural-endpoints"]').text()).not.toContain('3')
    expect(wrapper.text()).toContain('某位成员')
  })

  it('展示快照版本与时间', () => {
    const wrapper = mountPanel(makeEdge(), () => null)
    expect(wrapper.text()).toContain('v5')
    expect(wrapper.text()).toContain('2026-09-13T10:00:00')
  })
})

describe('StructuralRelationshipPanel 只读边界', () => {
  it('关闭只 emit close；待办跳转只 emit 事件；无 Bridge 操作控件', async () => {
    const wrapper = mountPanel(makeEdge(), (id) => `成员${id}`)
    expect(wrapper.text()).not.toContain('同意')
    expect(wrapper.text()).not.toContain('拒绝')

    await wrapper.find('[data-test="structural-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    await wrapper.find('[data-test="structural-correct"]').trigger('click')
    expect(wrapper.emitted('request-correction')).toHaveLength(1)

    await wrapper.find('[data-test="structural-view-todos"]').trigger('click')
    expect(wrapper.emitted('view-todos')).toHaveLength(1)
  })
})
