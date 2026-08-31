import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as statsApi from '@/api/stats'
import StatsView from '@/views/StatsView.vue'

vi.mock('@/api/stats', () => ({
  fetchStats: vi.fn(),
}))

const mockedFetchStats = vi.mocked(statsApi.fetchStats)

async function mountStats() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/stats', name: 'stats', component: StatsView },
    ],
  })
  await router.push('/stats')
  await router.isReady()
  const wrapper = mount(StatsView, { global: { plugins: [router] } })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, router }
}

describe('StatsView navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedFetchStats.mockResolvedValue({
      total: 0,
      by_gender: { m: 0, f: 0, unknown: 0 },
      generation_histogram: [],
      birthdays_this_month: [],
    })
  })

  it('统计页返回按钮使用 family-space 命名路由', async () => {
    const { wrapper, router } = await mountStats()

    await wrapper.find('[data-test="stats-back"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('family-space'))
    wrapper.unmount()
  })
})
