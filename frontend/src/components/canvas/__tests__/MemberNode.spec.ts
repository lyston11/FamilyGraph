import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'

import MemberNode from '@/components/canvas/MemberNode.vue'
import {
  HANDLE_SOURCE_BOTTOM,
  HANDLE_SOURCE_RIGHT,
  HANDLE_TARGET_LEFT,
  HANDLE_TARGET_TOP,
} from '@/composables/useFamilyTreeCanvas'
import type { PersonalFamilyViewDisplay } from '@/types/api'

/**
 * 家族树成员名牌（09-01 design.md §5.2，PersonalFamilyView 口径）：
 * - 纯展示：props 只收已解码 display + 可见性层级，不发请求、不读路由；
 * - 节点状态 icon + 文字：自己强调 / self_private / household_detail / lineage_summary；
 * - lineage_summary 无展开按钮、无家庭卡入口；masked 字段走 MaskedField 锁形章。
 * 断言走 data-test / 文本 / 行为，不依赖库内类名。
 */

function makeDisplay(overrides: Partial<PersonalFamilyViewDisplay> = {}): PersonalFamilyViewDisplay {
  return {
    id: 7,
    name: '林晚',
    gender: 'f',
    birth: { cal_type: 'solar', date: '1950-03-12' },
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'claimed',
    ...overrides,
  }
}

interface MountOptions {
  display?: PersonalFamilyViewDisplay
  visibilityLevel?: 'self_private' | 'household_detail' | 'lineage_summary'
  isSelf?: boolean
  term?: string | null
}

function mountNode({
  display = makeDisplay(),
  visibilityLevel = 'household_detail',
  isSelf = false,
  term = null,
}: MountOptions = {}) {
  return mount(MemberNode, {
    props: {
      id: `n-${display.id}`,
      data: { display, visibilityLevel, isSelf, term },
    },
    // Handle 依赖 VueFlow 节点注册表（无画布上下文时 onMounted 取不到 node），
    // 名牌自身的渲染/交互合同与连接点无关，stub 隔离
    global: { stubs: { Handle: true } },
  })
}

describe('MemberNode 纯展示（PersonalFamilyView 口径）', () => {
  it('渲染已解码 display：姓名、姓字纸牌、明文生日与称谓 chip', () => {
    const wrapper = mountNode({ term: '妈妈' })
    const card = wrapper.find('[data-test="canvas-member-card"]')
    expect(card.text()).toContain('林晚')
    expect(wrapper.find('.avatar').text()).toBe('林')
    expect(wrapper.find('[data-test="view-label"]').text()).toBe('妈妈')
    expect(wrapper.find('[data-test="node-birth"]').text()).toBe('1950-03-12')
  })

  it('masked 生日走 MaskedField 锁形章，不显示明文', () => {
    const wrapper = mountNode({ display: makeDisplay({ birth: { __masked__: true } }) })
    expect(wrapper.find('[data-test="masked-field"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="node-birth"]').exists()).toBe(false)
  })

  it('无称谓时不渲染称谓 chip；无生日不渲染日期行', () => {
    const wrapper = mountNode({ term: null, display: makeDisplay({ birth: null }) })
    expect(wrapper.find('[data-test="view-label"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="node-birth"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="masked-field"]').exists()).toBe(false)
  })

  it('自己节点：强调样式 + 「我」chip + self_private 徽章（icon + 文字）', () => {
    const wrapper = mountNode({ isSelf: true, visibilityLevel: 'self_private' })
    expect(wrapper.find('[data-test="canvas-member-card"]').classes()).toContain('is-self')
    expect(wrapper.find('[data-test="self-chip"]').text()).toBe('我')
    const badge = wrapper.find('[data-test="visibility-badge"]')
    expect(badge.text()).toContain('仅本人')
    expect(badge.find('svg').exists()).toBe(true)
  })

  it('household_detail：渲染「家庭可见」徽章', () => {
    const wrapper = mountNode({ visibilityLevel: 'household_detail' })
    expect(wrapper.find('[data-test="visibility-badge"]').text()).toContain('家庭可见')
  })

  it('lineage_summary：虚线摘要卡 + 「族谱摘要 · 不可展开」，无展开/家庭卡入口按钮', () => {
    const wrapper = mountNode({ visibilityLevel: 'lineage_summary' })
    const card = wrapper.find('[data-test="canvas-member-card"]')
    expect(card.classes()).toContain('summary-card')
    expect(wrapper.find('[data-test="visibility-badge"]').text()).toContain('不可展开')
    // 红线：summary 不可展开——不提供任何展开/申请进入按钮
    expect(wrapper.find('[data-test="join-request-btn"]').exists()).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('点击 / 回车仅 emit select(userId)——不发任何请求、不读路由', async () => {
    const wrapper = mountNode({})
    // 组件不引入 api/router 模块：无请求出口可言；交互只落在 select 事件
    await wrapper.find('[data-test="canvas-member-card"]').trigger('click')
    await wrapper.find('[data-test="canvas-member-card"]').trigger('keyup.enter')
    expect(wrapper.emitted('select')).toEqual([[7], [7]])
  })

  it('结构端口：四个端口全部显式 id（亲子上下、对称左右），与画布边单一来源', () => {
    const wrapper = mount(MemberNode, {
      props: {
        id: 'n-7',
        data: { display: makeDisplay(), visibilityLevel: 'household_detail', isSelf: false, term: null },
      },
      global: {
        stubs: {
          Handle: defineComponent({
            props: { id: { type: String, default: null }, type: { type: String, default: null }, position: { type: String, default: null } },
            template: '<div class="mock-handle" :data-handle-type="type" :data-handle-id="id" :data-handle-position="position" />',
          }),
        },
      },
    })
    const handles = wrapper.findAll('.mock-handle')
    // 四个端口 id 全部来自 useFamilyTreeCanvas 单一来源；Vue Flow 对未指定
    // handle 的边取「类型内第一个端口」，因此不允许任何无 id 端口存在
    const ids = handles.map((handle) => handle.attributes('data-handle-id'))
    expect(ids).toEqual([HANDLE_TARGET_TOP, HANDLE_TARGET_LEFT, HANDLE_SOURCE_RIGHT, HANDLE_SOURCE_BOTTOM])
    expect(ids.every((id) => typeof id === 'string' && id.length > 0)).toBe(true)
    const byId = new Map(handles.map((handle) => [handle.attributes('data-handle-id'), handle]))
    expect(byId.get(HANDLE_TARGET_TOP)?.attributes('data-handle-type')).toBe('target')
    expect(byId.get(HANDLE_TARGET_TOP)?.attributes('data-handle-position')).toBe('top')
    expect(byId.get(HANDLE_SOURCE_BOTTOM)?.attributes('data-handle-type')).toBe('source')
    expect(byId.get(HANDLE_SOURCE_BOTTOM)?.attributes('data-handle-position')).toBe('bottom')
    expect(byId.get(HANDLE_TARGET_LEFT)?.attributes('data-handle-position')).toBe('left')
    expect(byId.get(HANDLE_SOURCE_RIGHT)?.attributes('data-handle-position')).toBe('right')
  })
})
