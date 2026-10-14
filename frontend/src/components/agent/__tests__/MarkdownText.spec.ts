import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MarkdownText from '@/components/agent/MarkdownText.vue'

/** 助手正文渲染：markdown → 元素，且不产生可执行的 HTML（无 v-html）。 */

describe('MarkdownText', () => {
  it('把粗体、列表与行内代码渲染成元素', () => {
    const wrapper = mount(MarkdownText, {
      props: { text: '**依据：**\n\n- 直接路径 `#6`\n- 同辈' },
    })
    expect(wrapper.find('[data-test="markdown-text"]').exists()).toBe(true)
    expect(wrapper.find('strong').text()).toBe('依据：')
    const items = wrapper.findAll('li')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toBe('直接路径 #6')
    expect(wrapper.find('code').text()).toBe('#6')
    // 标记符号不再出现在可见文本里
    expect(wrapper.text()).not.toContain('**')
  })

  it('段内单换行渲染成换行元素', () => {
    const wrapper = mount(MarkdownText, { props: { text: '第一行\n第二行' } })
    expect(wrapper.findAll('p > br')).toHaveLength(1)
    expect(wrapper.text()).toContain('第一行')
    expect(wrapper.text()).toContain('第二行')
  })

  it('渲染标题与围栏代码块', () => {
    const wrapper = mount(MarkdownText, { props: { text: '## 依据\n\n```\n**原样**\n```' } })
    expect(wrapper.find('h4').text()).toBe('依据')
    expect(wrapper.find('pre code').text()).toBe('**原样**')
  })

  it('渲染管道表格（首轮真实验收里模型自己产出的形态）', () => {
    const wrapper = mount(MarkdownText, {
      props: { text: '| 来源层级 | 可用叫法 |\n|---|---|\n| 地区语言包 | 妻子 |' },
    })
    expect(wrapper.findAll('th').map((cell) => cell.text())).toEqual(['来源层级', '可用叫法'])
    expect(wrapper.findAll('td').map((cell) => cell.text())).toEqual(['地区语言包', '妻子'])
    expect(wrapper.text()).not.toContain('|')
  })

  it('尖括号内容只作为文本，绝不变成元素', () => {
    const payload = '<script>alert(1)</script><img src=x onerror=alert(2)>'
    const wrapper = mount(MarkdownText, { props: { text: payload } })
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toBe(payload)
  })

  it('空文本渲染空容器（不产生空段落）', () => {
    const wrapper = mount(MarkdownText, { props: { text: '' } })
    expect(wrapper.findAll('p')).toHaveLength(0)
    expect(wrapper.text()).toBe('')
  })
})
