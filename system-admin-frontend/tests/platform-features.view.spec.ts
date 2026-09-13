import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/platform-features', () => ({
  getPlatformFeatures: vi.fn(),
  updatePlatformFeatures: vi.fn(),
}))

import { getPlatformFeatures, updatePlatformFeatures } from '@/api/platform-features'
import PlatformFeaturesView from '@/views/PlatformFeaturesView.vue'
import type { AdminPlatformFeatureState } from '@/types/api'

const mockedGet = vi.mocked(getPlatformFeatures)
const mockedUpdate = vi.mocked(updatePlatformFeatures)

const stewardAssist = {
  candidate: false,
  ranking: false,
  explanation: false,
  candidate_source: 'environment' as const,
  ranking_source: 'environment' as const,
  explanation_source: 'environment' as const,
}

const baseState: AdminPlatformFeatureState = {
  memory_enabled: false,
  rag_enabled: true,
  memory_source: 'environment',
  rag_source: 'platform',
  steward_assist: { ...stewardAssist },
  updated_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedGet.mockResolvedValue({ ...baseState })
  mockedUpdate.mockResolvedValue({
    memory_enabled: true,
    rag_enabled: true,
    memory_source: 'platform',
    rag_source: 'platform',
    steward_assist: { ...stewardAssist },
    updated_at: '2026-09-13T00:00:00Z',
  })
})

describe('PlatformFeaturesView', () => {
  it('displays independent server state and source metadata', async () => {
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    expect(wrapper.get('[data-testid="platform-feature-source"]').text()).toContain('环境回退')
    expect(wrapper.get('[data-testid="platform-feature-memory-toggle"]').attributes('aria-checked')).toBe('false')
    expect(wrapper.get('[data-testid="platform-feature-rag-toggle"]').attributes('aria-checked')).toBe('true')
  })

  it('persists a toggle and uses the server response as the next visible state', async () => {
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()
    await wrapper.get('[data-testid="platform-feature-memory-toggle"]').trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith({ memory_enabled: true, rag_enabled: true })
    expect(wrapper.get('[data-testid="platform-feature-memory-toggle"]').attributes('aria-checked')).toBe('true')
    expect(wrapper.get('[data-testid="platform-feature-success"]').text()).toContain('Memory 已启用')
  })
})

describe('PlatformFeaturesView 管家辅助开关治理（09-13）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedGet.mockResolvedValue({
      ...baseState,
      steward_assist: {
        candidate: false,
        ranking: false,
        explanation: false,
        candidate_source: 'environment',
        ranking_source: 'environment',
        explanation_source: 'environment',
      },
    })
  })

  it('渲染三类管家辅助开关并显示服务端状态', async () => {
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    for (const kind of ['candidate', 'ranking', 'explanation']) {
      const toggle = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}"]`)
      expect(toggle.attributes('aria-checked')).toBe('false')
    }
  })

  it('点击候选补全开关 → 全量 PUT 三开关（点击项翻转，其余保持）', async () => {
    mockedGet.mockResolvedValue({
      ...baseState,
      steward_assist: {
        candidate: false,
        ranking: true,
        explanation: false,
        candidate_source: 'environment',
        ranking_source: 'platform',
        explanation_source: 'environment',
      },
    })
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    await wrapper.get('[data-testid="platform-feature-steward-assist-candidate"]').trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith({
      memory_enabled: false,
      rag_enabled: true,
      steward_assist_candidate: true,
      steward_assist_ranking: true,
      steward_assist_explanation: false,
    })
  })
})
