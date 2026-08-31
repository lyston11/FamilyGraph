import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MemberNode from '@/components/canvas/MemberNode.vue'
import type { Member } from '@/types/api'

/**
 * 画布成员名牌（design.md §3.2 / §5 测试策略）：
 * - 确档状态章：claimed=「已确档」实底 / 其余=「待确档」空心虚线章（身份状态机投影）；
 * - 称谓 chip 与摘要节点动作；纯展示组件不发起任何业务请求。
 * 断言走 data-test / 文本 / 行为，不依赖库内类名。
 */

function makeMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 7,
    name: '林晚',
    is_admin: false,
    gender: 'f',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'managed',
    created_by: 1,
    created_at: '2026-08-25T00:00:00',
    clan_disclosure: { avatar: false, photos: false, dates: false, bio: false, attachments: false },
    permissions: { edit: true, delete: true },
    ...overrides,
  }
}

function mountNode(data: {
  member?: Member
  viewLabel?: string | null
  summary?: boolean
}) {
  return mount(MemberNode, {
    props: {
      id: `n-${data.member?.id ?? 0}`,
      data: {
        member: data.member,
        viewLabel: data.viewLabel ?? null,
        summary: data.summary ?? false,
      },
    },
    // Handle 依赖 VueFlow 节点注册表（无画布上下文时 onMounted 取不到 node），
    // 名牌自身的渲染/交互合同与连接点无关，stub 隔离
    global: { stubs: { Handle: true } },
  })
}

describe('MemberNode 确档状态徽章', () => {
  it('claimed：渲染「已确档」实底徽章（fg-badge--confirmed）', () => {
    const wrapper = mountNode({ member: makeMember({ claim_status: 'claimed' }) })
    const stamp = wrapper.find('[data-test="identity-stamp"]')
    expect(stamp.exists()).toBe(true)
    expect(stamp.text()).toBe('已确档')
    expect(stamp.classes()).toContain('fg-badge--confirmed')
  })

  it('managed（provisional 人物）：渲染「待确档」空心虚线徽章（fg-badge--provisional）', () => {
    const wrapper = mountNode({ member: makeMember({ claim_status: 'managed' }) })
    const stamp = wrapper.find('[data-test="identity-stamp"]')
    expect(stamp.text()).toBe('待确档')
    expect(stamp.classes()).toContain('fg-badge--provisional')
  })
})

describe('MemberNode 展示与行为', () => {
  it('渲染姓名、姓字纸牌与称谓 chip', () => {
    const wrapper = mountNode({ member: makeMember(), viewLabel: '妈妈' })
    expect(wrapper.find('[data-test="canvas-member-card"]').text()).toContain('林晚')
    expect(wrapper.find('[data-test="view-label"]').text()).toBe('妈妈')
    expect(wrapper.find('.avatar').text()).toBe('林')
  })

  it('无 viewLabel 时不渲染称谓 chip；性别文案随 gender', () => {
    const wrapper = mountNode({ member: makeMember({ gender: 'm' }) })
    expect(wrapper.find('[data-test="view-label"]').exists()).toBe(false)
    expect(wrapper.find('.gender').text()).toBe('男')
  })

  it('摘要节点：虚线卡 + 「申请进入 TA 的家庭空间」动作 emits join（不冒泡到 open）', async () => {
    const wrapper = mountNode({ member: makeMember(), summary: true })
    expect(wrapper.find('.summary-card').exists()).toBe(true)
    await wrapper.find('[data-test="join-request-btn"]').trigger('click')
    expect(wrapper.emitted('join')).toEqual([[7]])
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('点击卡片 / 回车键 emits open（键盘可达基线）', async () => {
    const wrapper = mountNode({ member: makeMember() })
    await wrapper.find('[data-test="canvas-member-card"]').trigger('click')
    await wrapper.find('[data-test="canvas-member-card"]').trigger('keyup.enter')
    expect(wrapper.emitted('open')).toEqual([[7], [7]])
  })

  it('成员记录未到齐时渲染占位卡，不出空壳', () => {
    const wrapper = mountNode({})
    const card = wrapper.find('[data-test="canvas-member-card"]')
    expect(card.exists()).toBe(true)
    expect(card.classes()).toContain('member-node--placeholder')
    expect(wrapper.find('[data-test="identity-stamp"]').exists()).toBe(false)
  })
})
