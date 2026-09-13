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

const baseState: AdminPlatformFeatureState = {
  memory_enabled: false,
  rag_enabled: true,
  memory_source: 'environment',
  rag_source: 'platform',
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
