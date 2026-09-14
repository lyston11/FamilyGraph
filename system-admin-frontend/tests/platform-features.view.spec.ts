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

const assistCases = [
  { kind: 'candidate', label: '候选补全' },
  { kind: 'ranking', label: '推荐排序' },
  { kind: 'explanation', label: '卡片解释' },
  { kind: 'terminology', label: '称谓优化' },
] as const

const featureCases = [
  { key: 'memory', label: 'Memory' },
  { key: 'rag', label: 'RAG' },
] as const

const stewardAssist = {
  candidate: false,
  ranking: false,
  explanation: false,
  terminology: false,
  candidate_source: 'environment' as const,
  ranking_source: 'environment' as const,
  explanation_source: 'environment' as const,
  terminology_source: 'environment' as const,
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

  it.each(featureCases)('$label 正常停用后提示与服务端状态一致', async ({ key, label }) => {
    const enabledState: AdminPlatformFeatureState = {
      ...baseState,
      memory_enabled: true,
      memory_source: 'platform',
    }
    mockedGet.mockResolvedValue(enabledState)
    mockedUpdate.mockResolvedValue({ ...enabledState, [`${key}_enabled`]: false })
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    await wrapper.get(`[data-testid="platform-feature-${key}-toggle"]`).trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith({
      memory_enabled: key !== 'memory',
      rag_enabled: key !== 'rag',
    })
    expect(wrapper.get(`[data-testid="platform-feature-${key}-toggle"]`).attributes('aria-checked')).toBe('false')
    expect(wrapper.get('[data-testid="platform-feature-success"]').text()).toContain(`${label} 已停用`)
  })

  it.each(featureCases)('$label 请求开启但响应为部署关闭时不提示已启用', async ({ key, label }) => {
    const disabledState: AdminPlatformFeatureState = {
      ...baseState,
      rag_enabled: false,
      rag_source: 'environment',
    }
    mockedGet.mockResolvedValue(disabledState)
    mockedUpdate.mockResolvedValue({ ...disabledState, [`${key}_source`]: 'deployment' })
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    const toggle = wrapper.get(`[data-testid="platform-feature-${key}-toggle"]`)
    await toggle.trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith({
      memory_enabled: key === 'memory',
      rag_enabled: key === 'rag',
    })
    const notice = wrapper.get('[data-testid="platform-feature-success"]').text()
    expect(notice).not.toContain('已启用')
    expect(notice).toContain(label)
    expect(notice).toContain('运维调整部署配置')
    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(toggle.attributes('disabled')).toBeDefined()
  })
})

describe('PlatformFeaturesView 管家辅助开关治理（09-13）', () => {
  it('渲染四类管家辅助开关并显示服务端状态与环境来源', async () => {
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    for (const { kind, label } of assistCases) {
      const toggle = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}"]`)
      expect(toggle.attributes('aria-checked')).toBe('false')
      expect(toggle.attributes('disabled')).toBeUndefined()
      expect(toggle.text()).toContain(label)
      const source = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}-source"]`)
      expect(source.text()).toContain('环境回退')
      expect(toggle.attributes('aria-describedby')).toBe(source.attributes('id'))
    }
  })

  it.each(assistCases)('$label 请求开启但部署关闭时，提示和开关均以响应为准', async ({ kind, label }) => {
    mockedUpdate.mockResolvedValue({
      ...baseState,
      steward_assist: {
        ...stewardAssist,
        [`${kind}_source`]: 'deployment',
      },
    })
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()
    const toggle = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}"]`)

    await toggle.trigger('click')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith(expect.objectContaining({
      [`steward_assist_${kind}`]: true,
    }))
    const notice = wrapper.get('[data-testid="platform-feature-success"]').text()
    expect(notice).not.toContain('已启用')
    expect(notice).toContain(label)
    expect(notice).toContain('部署')
    expect(notice).toContain('运维')
    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(toggle.attributes('disabled')).toBeDefined()
    expect(toggle.text()).toContain('部署关闭')
    const source = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}-source"]`).text()
    expect(source).toContain('部署级关闭')
    expect(source).toContain('部署配置')

    await toggle.trigger('click')
    expect(mockedUpdate).toHaveBeenCalledTimes(1)
  })

  it.each(assistCases)('$label 正常开启与停用使用中文提示并同步平台来源', async ({ kind, label }) => {
    const enabledState: AdminPlatformFeatureState = {
      ...baseState,
      steward_assist: {
        ...stewardAssist,
        [kind]: true,
        [`${kind}_source`]: 'platform',
      },
    }
    mockedUpdate.mockResolvedValue(enabledState)
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()
    const toggle = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}"]`)

    await toggle.trigger('click')
    await flushPromises()

    expect(toggle.attributes('aria-checked')).toBe('true')
    expect(toggle.attributes('disabled')).toBeUndefined()
    const enabledNotice = wrapper.get('[data-testid="platform-feature-success"]').text()
    expect(enabledNotice).toContain(`管家辅助（${label}）`)
    expect(enabledNotice).toContain('已启用')
    expect(enabledNotice).not.toContain(kind)
    expect(wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}-source"]`).text()).toContain('平台配置')
    expect(mockedUpdate).toHaveBeenNthCalledWith(1, expect.objectContaining({
      [`steward_assist_${kind}`]: true,
    }))

    mockedUpdate.mockResolvedValue({
      ...enabledState,
      steward_assist: { ...enabledState.steward_assist, [kind]: false },
    })
    await toggle.trigger('click')
    await flushPromises()

    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(toggle.attributes('disabled')).toBeUndefined()
    const disabledNotice = wrapper.get('[data-testid="platform-feature-success"]').text()
    expect(disabledNotice).toContain(`管家辅助（${label}）`)
    expect(disabledNotice).toContain('已停用')
    expect(mockedUpdate).toHaveBeenNthCalledWith(2, expect.objectContaining({
      memory_enabled: false,
      rag_enabled: true,
      [`steward_assist_${kind}`]: false,
    }))
  })

  it.each(assistCases)('$label 已知部署关闭时显示运维指引并阻止无效请求', async ({ kind, label }) => {
    mockedGet.mockResolvedValue({
      ...baseState,
      steward_assist: { ...stewardAssist, [`${kind}_source`]: 'deployment' },
    })
    const wrapper = mount(PlatformFeaturesView)
    await flushPromises()

    const toggle = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}"]`)
    expect(toggle.attributes('aria-checked')).toBe('false')
    expect(toggle.attributes('aria-label')).toContain(`${label}：部署关闭`)
    expect(toggle.attributes('disabled')).toBeDefined()
    const source = wrapper.get(`[data-testid="platform-feature-steward-assist-${kind}-source"]`).text()
    expect(source).toContain('部署级关闭')
    expect(source).toContain('运维调整部署配置后刷新页面')
    for (const other of assistCases.filter((assist) => assist.kind !== kind)) {
      expect(wrapper.get(`[data-testid="platform-feature-steward-assist-${other.kind}"]`).attributes('disabled')).toBeUndefined()
    }

    await toggle.trigger('click')
    expect(mockedUpdate).not.toHaveBeenCalled()
  })

  it('点击候选补全开关 → 全量 PUT 四开关（点击项翻转，其余保持）', async () => {
    mockedGet.mockResolvedValue({
      ...baseState,
      steward_assist: {
        candidate: false,
        ranking: true,
        explanation: false,
        candidate_source: 'environment',
        ranking_source: 'platform',
        explanation_source: 'environment',
        terminology: false,
        terminology_source: 'environment',
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
      steward_assist_terminology: false,
    })
  })
})
