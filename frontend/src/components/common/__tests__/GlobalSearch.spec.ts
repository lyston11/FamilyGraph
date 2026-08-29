import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GlobalSearch from '@/components/common/GlobalSearch.vue'
import * as statsApi from '@/api/stats'
import type { SearchHit } from '@/api/stats'

/**
 * 全局搜索（P4-4）：名字/称谓前缀匹配 → 结果下拉；点击命中跳档案列表。
 * 下拉为页内绝对定位（无 teleport，壳导航堆叠上下文内），样式随主题 token。
 * 键盘可达：Esc 收起 / 方向键移动高亮 / 回车选择；点击外部收起。
 */

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }))

vi.mock('@/api/stats', () => ({
  fetchStats: vi.fn(),
  search: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock }),
}))

const mockedSearch = vi.mocked(statsApi.search)

const hits: SearchHit[] = [
  { id: 1, name: '张三', level: 'full' },
  { id: 2, name: '李四', level: 'summary' },
]

function typeQuery(wrapper: ReturnType<typeof mount>, value: string): void {
  ;(wrapper.find('[data-test="search-input"] input').element as HTMLInputElement).value = value
  wrapper.find('[data-test="search-input"] input').trigger('input')
}

function pressKey(wrapper: ReturnType<typeof mount>, key: string): void {
  wrapper.find('[data-test="search-input"] input').trigger('keydown', { key })
}

async function searchAndWait(wrapper: ReturnType<typeof mount>, query: string): Promise<void> {
  typeQuery(wrapper, query)
  await new Promise((resolve) => setTimeout(resolve, 300))
}

describe('GlobalSearch', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('输入命中 → 下拉展示名字与可见性等级', async () => {
    mockedSearch.mockResolvedValue(hits)
    const wrapper = mount(GlobalSearch)
    await searchAndWait(wrapper, '张')

    expect(mockedSearch).toHaveBeenCalledWith('张')
    const items = wrapper.findAll('[data-test="search-hit"]')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('张三')
    expect(items[0].text()).toContain('可查看详情')
    expect(items[1].text()).toContain('仅摘要可见')
    wrapper.unmount()
  })

  it('无命中 → 明确空态文案；清空 → 收起下拉', async () => {
    mockedSearch.mockResolvedValue([])
    const wrapper = mount(GlobalSearch)
    await searchAndWait(wrapper, '王')
    expect(wrapper.find('[data-test="search-empty"]').text()).toContain('未找到相关家人')

    typeQuery(wrapper, '')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="search-empty"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="search-results"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('Esc 收起下拉（结果与空态都不再显示）', async () => {
    mockedSearch.mockResolvedValue(hits)
    const wrapper = mount(GlobalSearch)
    await searchAndWait(wrapper, '张')
    expect(wrapper.find('[data-test="search-results"]').exists()).toBe(true)

    pressKey(wrapper, 'Escape')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="search-results"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="search-empty"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('方向键移动高亮、回车选择命中并跳档案列表', async () => {
    mockedSearch.mockResolvedValue(hits)
    const wrapper = mount(GlobalSearch)
    await searchAndWait(wrapper, '张')

    pressKey(wrapper, 'ArrowDown')
    await wrapper.vm.$nextTick()
    // 高亮第一项（aria-selected）
    expect(wrapper.find('[data-test="search-hit"][aria-selected="true"]').text()).toContain('张三')

    pressKey(wrapper, 'ArrowDown')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="search-hit"][aria-selected="true"]').text()).toContain('李四')

    pressKey(wrapper, 'Enter')
    await wrapper.vm.$nextTick()
    expect(pushMock).toHaveBeenCalledWith({ name: 'home', query: { highlight: '2' } })
    wrapper.unmount()
  })

  it('点击搜索框外部收起下拉', async () => {
    mockedSearch.mockResolvedValue(hits)
    const wrapper = mount(GlobalSearch, { attachTo: document.body })
    await searchAndWait(wrapper, '张')
    expect(wrapper.find('[data-test="search-results"]').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="search-results"]').exists()).toBe(false)
    wrapper.unmount()
    document.body.innerHTML = ''
  })
})
