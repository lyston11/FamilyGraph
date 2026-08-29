import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import ActionCardItem from '@/components/actioncard/ActionCardItem.vue'
import ErrorNotice from '@/components/agent/ErrorNotice.vue'
import MessageList from '@/components/agent/MessageList.vue'
import ScopeBanner from '@/components/agent/ScopeBanner.vue'
import SessionList from '@/components/agent/SessionList.vue'
import WebCitationList from '@/components/agent/WebCitationList.vue'
import { useActionCardsStore } from '@/stores/actionCards'
import type { AgentSession } from '@/types/agent'

/** 消息流原语组件：气泡、工具 chip、进行中指示、错误文案映射、scope 徽标、引用来源 */

describe('MessageList', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('渲染文本气泡与角色方向', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [
          { id: 1, role: 'user', text: '谁是我的长辈？', createdAt: null, status: 'sent' },
          { id: 2, role: 'assistant', text: '根据可见路径…', createdAt: null, status: 'sent' },
        ],
        toolSummaries: [],
        run: null,
      },
    })
    const items = wrapper.findAll('[data-test="message-item"]')
    expect(items).toHaveLength(2)
    expect(items[0].attributes('data-role')).toBe('user')
    expect(items[1].text()).toContain('根据可见路径…')
    // 不渲染任何内部字段
    expect(wrapper.text()).not.toContain('idempotency')
    expect(wrapper.text()).not.toContain('policy_version')
  })

  it('助手消息中的 card_ids 与空间 Inbox 共用 ActionCardItem', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const actionCards = useActionCardsStore(pinia)
    actionCards.partitions.set(7, {
      cards: [
        {
          id: 3,
          kind: 'lineage_request',
          space_id: 7,
          subject_user: { id: 10, name: '张三' },
          object_user: { id: 11, name: '李四' },
          reason_text: '已确认亲属关系',
          evidence: { fact_ids: [9], path_summary: '张三 → 李四', evidence_version: 1 },
          proposed_action: { type: 'request_lineage', params: { space_id: 7 } },
          privacy_effect: '仅共享族谱摘要',
          state: 'pending',
          expires_at: null,
          created_at: '2026-08-26T00:00:00',
          revision: 1,
        },
      ],
      loaded: true,
      loading: false,
      hidden: false,
      error: null,
    })
    // ActionCardItem 内部 useMessage 需要 NMessageProvider 祖先（App 层已备好）
    const Harness = defineComponent({
      render() {
        return h('div', [
          h(NMessageProvider, () =>
            h(MessageList, {
              messages: [
                {
                  id: 2,
                  role: 'assistant',
                  text: '我找到一条建议。',
                  createdAt: null,
                  status: 'sent',
                  cardIds: [3],
                },
              ],
              toolSummaries: [],
              run: null,
              spaceId: 7,
            }),
          ),
        ])
      },
    })
    const wrapper = mount(Harness, { global: { plugins: [pinia] } })
    expect(wrapper.find('[data-test="message-cards"]').exists()).toBe(true)
    expect(wrapper.findComponent(ActionCardItem).exists()).toBe(true)
    expect(wrapper.find('[data-test="card-title"]').text()).toContain('加入族谱空间建议')
  })

  it('工具摘要 chip：图标 + tool_name + 成功/失败，不含原始 payload', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        toolSummaries: [
          { toolCallId: 't1', toolName: 'fg_search_space', status: 'ok' },
          { toolCallId: 't2', toolName: 'fg_get_relationship_path', status: 'running' },
        ],
        run: null,
      },
    })
    const chips = wrapper.findAll('[data-test="tool-chip"]')
    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('fg_search_space')
    expect(chips[0].classes()).toContain('ok')
    expect(chips[1].classes()).toContain('running')
    // 原始 payload 字段不出现
    expect(wrapper.text()).not.toContain('tool_call_id')
  })

  it('Run 进行中且尚无回复 → 显示思考指示与 live region 播报', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [{ id: 1, role: 'user', text: '在吗', createdAt: null, status: 'sent' }],
        toolSummaries: [],
        run: { id: 9, status: 'running', terminal: false },
      },
    })
    expect(wrapper.find('[data-test="thinking-indicator"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="live-region"]').attributes('aria-live')).toBe('polite')
    expect(wrapper.find('[data-test="live-region"]').text()).toContain('正在思考')
  })

  it('终态后不再显示思考指示', () => {
    const wrapper = mount(MessageList, {
      props: {
        messages: [],
        toolSummaries: [],
        run: { id: 9, status: 'cancelled', terminal: true },
      },
    })
    expect(wrapper.find('[data-test="thinking-indicator"]').exists()).toBe(false)
  })
})

describe('ErrorNotice', () => {
  it('结构化错误码映射为中文文案，不透传 detail JSON', () => {
    const wrapper = mount(ErrorNotice, {
      props: { error: { code: 'AGENT_RUN_LIMIT', message: '' } },
    })
    expect(wrapper.find('[data-test="error-notice"]').text()).toContain('并发任务较多')
    expect(wrapper.find('[data-test="error-retry"]').exists()).toBe(false)
  })

  it('STREAM_LOST 提供重试入口', async () => {
    const wrapper = mount(ErrorNotice, {
      props: { error: { code: 'STREAM_LOST', message: '' } },
    })
    expect(wrapper.text()).toContain('连接中断')
    await wrapper.find('[data-test="error-retry"]').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('error=null 不渲染', () => {
    const wrapper = mount(ErrorNotice, {
      props: { error: null },
    })
    expect(wrapper.find('[data-test="error-notice"]').exists()).toBe(false)
  })
})

describe('ScopeBanner', () => {
  it('始终展示空间名称与 kind 徽标（发送前可确认 scope）', () => {
    const wrapper = mount(ScopeBanner, {
      props: {
        space: { id: 2, name: '宗族', owner_id: 1, kind: 'lineage', created_at: '2026-08-26T00:00:00', pending_count: 0, member_count: 3 },
      },
    })
    expect(wrapper.text()).toContain('宗族')
    expect(wrapper.text()).toContain('宗族空间')
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
  })

  it('无空间 → 明确提示', () => {
    const wrapper = mount(ScopeBanner, {
      props: { space: null },
    })
    expect(wrapper.text()).toContain('暂无可用空间')
  })
})

describe('SessionList', () => {
  it('新建会话按钮上抛 create；标题回退时间格式', () => {
    const sessions: AgentSession[] = [
      { id: 11, space_id: 1, agent_kind: 'assistant', created_at: '2026-08-26T09:30:00' },
    ]
    const wrapper = mount(SessionList, {
      props: { sessions, activeSessionId: 11, titles: {} },
    })
    expect(wrapper.text()).not.toContain('谁是我的长辈') // 未加载历史 → 用回退标题
    const btn = wrapper.find('[data-test="new-session-btn"]')
    btn.trigger('click')
    expect(wrapper.emitted('create')).toHaveLength(1)
  })

  it('已加载历史的会话优先使用传入标题（store 层截断，见 agent.spec）', async () => {
    const sessions: AgentSession[] = [
      { id: 11, space_id: 1, agent_kind: 'assistant', created_at: '2026-08-26T09:30:00' },
    ]
    const wrapper = mount(SessionList, {
      props: { sessions, activeSessionId: 11, titles: { 11: '谁是我的长辈？' } },
    })
    // n-select 选中项标签渲染在 base-selection 内：等待一次渲染队列
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('谁是我的长辈？')
  })
})

describe('WebCitationList（受控联网引用，占位不劣化）', () => {
  const citation = {
    url: 'https://www.example.com/张氏源流',
    title: '张氏源流考',
    excerpt: '张氏出自姬姓…',
    fetched_at: '2026-08-26T00:00:00',
    trust: 'external' as const,
  }

  it('标题链接 + 域名徽章 + 外部标签（域名/用途标签视觉）', () => {
    const wrapper = mount(WebCitationList, { props: { citations: [citation] } })
    expect(wrapper.find('[data-test="web-citation-list"]').exists()).toBe(true)
    const link = wrapper.find('a.title')
    expect(link.attributes('href')).toBe(citation.url)
    expect(link.attributes('rel')).toContain('noopener')
    expect(wrapper.find('[data-test="web-citation-domain"]').text()).toContain('www.example.com')
    expect(wrapper.find('[data-test="web-citation-external"]').text()).toContain('外部')
    expect(wrapper.text()).toContain('未经家谱确认')
  })

  it('compact 模式不渲染摘要正文；空引用不渲染', () => {
    const wrapper = mount(WebCitationList, { props: { citations: [citation], compact: true } })
    expect(wrapper.find('.excerpt').exists()).toBe(false)
    expect(wrapper.find('[data-test="web-citation-list"]').exists()).toBe(true)

    const empty = mount(WebCitationList, { props: { citations: [] } })
    expect(empty.find('[data-test="web-citation-list"]').exists()).toBe(false)
  })
})
