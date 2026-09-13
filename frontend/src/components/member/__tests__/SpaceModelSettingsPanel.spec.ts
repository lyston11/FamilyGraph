import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider, NSelect } from 'naive-ui'
import { defineComponent, h } from 'vue'

import {
  fetchSpaceModelSettings,
  updateSpaceModelSetting,
} from '@/api/spaceModelSettings'
import SpaceModelSettingsPanel from '@/components/member/SpaceModelSettingsPanel.vue'
import type {
  AgentConfigKind,
  AgentModelCatalogEntry,
  SpaceAgentSetting,
  SpaceModelSettings,
} from '@/types/agent'

vi.mock('@/api/spaceModelSettings', () => ({
  fetchSpaceModelSettings: vi.fn(),
  updateSpaceModelSetting: vi.fn().mockResolvedValue({}),
  resetSpaceModelSetting: vi.fn().mockResolvedValue(undefined),
}))

const mockedFetch = vi.mocked(fetchSpaceModelSettings)
const mockedUpdate = vi.mocked(updateSpaceModelSetting)

/** 目录：云 Provider（id 3）双模型，用于下拉切换与云同意开关渲染 */
function cloudEntry(): AgentModelCatalogEntry {
  return {
    provider_id: 3,
    name: 'liu-dada',
    kind: 'openai_compatible',
    api: 'openai-responses',
    models: ['gpt-5.6-sol', 'gpt-5.7'],
  }
}

function enabledRow(
  kind: AgentConfigKind,
  overrides: Partial<SpaceAgentSetting> = {},
): SpaceAgentSetting {
  return {
    agent_kind: kind,
    provider_id: 3,
    model: 'gpt-5.6-sol',
    cloud_allowed: false,
    local_required: false,
    enabled: true,
    assist_candidate: false,
    assist_ranking: false,
    assist_explanation: false,
    assist_candidate_effective: false,
    assist_ranking_effective: false,
    assist_explanation_effective: false,
    inferred_tree: false,
    inferred_effective: false,
    ...overrides,
  }
}

function settingsFixture(
  stewardOverrides: Partial<SpaceAgentSetting> = {},
): SpaceModelSettings {
  return {
    space_id: 7,
    settings: {
      assistant: null,
      steward: {
        agent_kind: 'steward',
        provider_id: 3,
        model: 'gpt-5.6-sol',
        cloud_allowed: true,
        local_required: false,
        enabled: true,
        assist_candidate: true,
        assist_ranking: false,
        assist_explanation: false,
        assist_candidate_effective: true,
        assist_ranking_effective: false,
        assist_explanation_effective: false,
        inferred_tree: false,
        inferred_effective: false,
        ...stewardOverrides,
      },
    },
    catalog: [cloudEntry()],
    platform_default: { assistant: null, steward: null, updated_at: null },
  }
}

/** 无落库行：仅云 Provider 目录（表单为空，云开关不渲染） */
function emptySettingsFixture(): SpaceModelSettings {
  return {
    space_id: 7,
    settings: { assistant: null, steward: null },
    catalog: [cloudEntry()],
    platform_default: { assistant: null, steward: null, updated_at: null },
  }
}

/** 无落库行 + 平台默认预填（事故现场：预填云模型，观感"已配置"实未落库） */
function prefilledFixture(kind: AgentConfigKind): SpaceModelSettings {
  return {
    ...emptySettingsFixture(),
    platform_default: {
      assistant: kind === 'assistant' ? { provider_id: 3, model: 'gpt-5.6-sol' } : null,
      steward: kind === 'steward' ? { provider_id: 3, model: 'gpt-5.6-sol' } : null,
      updated_at: null,
    },
  }
}

let pinia: Pinia

function mountPanel() {
  const Harness = defineComponent({
    render() {
      return h('div', [h(NMessageProvider, () => h(SpaceModelSettingsPanel, { spaceId: 7 }))])
    },
  })
  return mount(Harness, { global: { plugins: [pinia] }, attachTo: document.body })
}

beforeEach(() => {
  pinia = createPinia()
  vi.clearAllMocks()
  mockedFetch.mockResolvedValue(settingsFixture())
})

describe('SpaceModelSettingsPanel（管家模型辅助开关）', () => {
  it('steward 区块渲染三个辅助开关且初值来自行级设置；assistant 区块无开关', async () => {
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-settings-steward"]').exists()).toBe(true)
    })

    const stewardFlags = wrapper.find('[data-test="assist-flags-steward"]')
    expect(stewardFlags.exists()).toBe(true)
    const switches = stewardFlags.findAll('.n-switch')
    expect(switches.length).toBe(4)
    // 候选开、排序关、解释关、推测层关（初值来自行级设置；naive-ui 用 n-switch--active 表达开态）
    const states = switches.map((node) => node.classes().includes('n-switch--active'))
    expect(states).toEqual([true, false, false, false])

    const assistantBlock = wrapper.find('[data-test="model-settings-assistant"]')
    expect(assistantBlock.find('[data-test="assist-flags-assistant"]').exists()).toBe(false)
  })

  it('保存 steward 设置时随载荷提交 assist_* 开关', async () => {
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-save-steward"]').exists()).toBe(true)
    })

    await wrapper.find('[data-test="model-save-steward"]').trigger('click')
    await vi.waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalled()
    })
    const payload = mockedUpdate.mock.calls[0][1]
    expect(payload.agent_kind).toBe('steward')
    expect(payload.assist_candidate).toBe(true)
    expect(payload.assist_ranking).toBe(false)
    expect(payload.assist_explanation).toBe(false)
    expect(payload.inferred_tree).toBe(false)
  })
})

/** 保存后重载用的 fixture：assistant 行 = 预填默认 + 已同意云（供状态行刷新断言） */
function savedAssistantFixture(): SpaceModelSettings {
  return {
    ...prefilledFixture('assistant'),
    settings: {
      assistant: enabledRow('assistant', { cloud_allowed: true }),
      steward: null,
    },
  }
}

describe('SpaceModelSettingsPanel（开关即保存与未保存徽标，09-06 UX 复盘）', () => {
  beforeEach(() => {
    pinia = createPinia()
    vi.clearAllMocks()
    mockedFetch.mockResolvedValue(settingsFixture())
  })

  it('事故现场：预填平台默认 + 翻开云同意开关 → 立即落库并刷新状态行', async () => {
    mockedFetch.mockReset()
    mockedFetch
      .mockResolvedValueOnce(prefilledFixture('assistant'))
      .mockResolvedValueOnce(savedAssistantFixture())
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="cloud-switch"]').exists()).toBe(true)
    })
    await wrapper.find('[data-test="cloud-switch"]').trigger('click')

    await vi.waitFor(() => expect(mockedUpdate).toHaveBeenCalledTimes(1))
    expect(mockedUpdate.mock.calls[0][1]).toMatchObject({
      agent_kind: 'assistant',
      provider_id: 3,
      model: 'gpt-5.6-sol',
      cloud_allowed: true,
      enabled: true,
    })
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-status-assistant"]').text()).toContain('已同意云端执行')
    })
  })

  it('云同意开关保存失败 → 非乐观回弹，状态行不变', async () => {
    mockedFetch.mockReset()
    mockedFetch.mockResolvedValue(prefilledFixture('assistant'))
    mockedUpdate.mockRejectedValueOnce(new Error('boom'))
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="cloud-switch"]').exists()).toBe(true)
    })
    await wrapper.find('[data-test="cloud-switch"]').trigger('click')

    await vi.waitFor(() => expect(mockedUpdate).toHaveBeenCalledTimes(1))
    await flushPromises()
    expect(wrapper.find('[data-test="cloud-switch"]').classes()).not.toContain('n-switch--active')
    expect(wrapper.find('[data-test="model-status-assistant"]').text()).toContain('继承平台默认')
  })

  it('表单未选全（模型被清空）时翻云开关被守卫拦截', async () => {
    mockedFetch.mockResolvedValue(prefilledFixture('assistant'))
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="cloud-switch"]').exists()).toBe(true)
    })
    const modelSelect = wrapper
      .find('[data-test="model-settings-assistant"]')
      .findAllComponents(NSelect)[1]!
    await modelSelect.vm.$emit('update:value', null)
    await flushPromises()
    await wrapper.find('[data-test="cloud-switch"]').trigger('click')
    await flushPromises()
    expect(mockedUpdate).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="cloud-switch"]').classes()).not.toContain('n-switch--active')
  })

  it('steward 辅助开关翻开即落库，整行保留行上字段', async () => {
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="assist-switch-ranking"]').exists()).toBe(true)
    })
    await wrapper.find('[data-test="assist-switch-ranking"]').trigger('click')

    await vi.waitFor(() => expect(mockedUpdate).toHaveBeenCalledTimes(1))
    // 行上 provider/model/cloud/其余 flag 原样保留，仅目标 flag 翻转
    expect(mockedUpdate.mock.calls[0][1]).toMatchObject({
      agent_kind: 'steward',
      provider_id: 3,
      model: 'gpt-5.6-sol',
      cloud_allowed: true,
      enabled: true,
      assist_candidate: true,
      assist_ranking: true,
      assist_explanation: false,
      inferred_tree: false,
    })
  })

  it('无启用行且表单未选全 → 辅助开关禁用并提示先配置管家模型', async () => {
    mockedFetch.mockResolvedValue(emptySettingsFixture())
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="assist-guard-hint"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="assist-guard-hint"]').text()).toBe('先配置管家模型')
    expect(wrapper.find('[data-test="assist-switch-candidate"]').classes()).toContain(
      'n-switch--disabled',
    )
  })

  it('未保存徽标：下拉改动出现，保存后消失', async () => {
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-save-steward"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="model-unsaved-steward"]').exists()).toBe(false)
    const modelSelect = wrapper
      .find('[data-test="model-settings-steward"]')
      .findAllComponents(NSelect)[1]!
    await modelSelect.vm.$emit('update:value', 'gpt-5.7')
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-unsaved-steward"]').exists()).toBe(true)
    })
    await wrapper.find('[data-test="model-save-steward"]').trigger('click')
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-unsaved-steward"]').exists()).toBe(false)
    })
  })

  it('纯预填（无落库行）不触发未保存徽标', async () => {
    mockedFetch.mockResolvedValue(prefilledFixture('assistant'))
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-settings-assistant"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="model-unsaved-assistant"]').exists()).toBe(false)
  })
})

describe('SpaceModelSettingsPanel（推测层开关，09-13）', () => {
  beforeEach(() => {
    pinia = createPinia()
    vi.clearAllMocks()
  })

  it('推测关系上树开关初值来自行级设置；翻开即随载荷提交 inferred_tree', async () => {
    mockedFetch.mockResolvedValue(settingsFixture())
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="assist-flags-steward"]').exists()).toBe(true)
    })
    const inferredSwitch = wrapper.find('[data-test="assist-switch-inferred"]')
    expect(inferredSwitch.exists()).toBe(true)
    await inferredSwitch.trigger('click')
    await vi.waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalled()
    })
    const payload = mockedUpdate.mock.calls[0][1]
    expect(payload.agent_kind).toBe('steward')
    expect(payload.inferred_tree).toBe(true)
  })

  it('空间级开而平台级未开 → 显示可解释的平台提示', async () => {
    mockedFetch.mockResolvedValue(settingsFixture({ inferred_tree: true, inferred_effective: false }))
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="inferred-platform-hint"]').exists()).toBe(true)
    })
  })

  it('推测层生效（平台已开）→ 不显示平台提示', async () => {
    mockedFetch.mockResolvedValue(settingsFixture({ inferred_effective: true }))
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="model-settings-steward"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="inferred-platform-hint"]').exists()).toBe(false)
  })
})

describe('SpaceModelSettingsPanel（辅助开关平台治理提示，09-13）', () => {
  beforeEach(() => {
    pinia = createPinia()
    vi.clearAllMocks()
    mockedFetch.mockResolvedValue(settingsFixture())
  })

  it('空间级辅助开而平台未开 → 显示可解释的平台提示', async () => {
    mockedFetch.mockResolvedValue(
      settingsFixture({ assist_candidate_effective: false, assist_ranking_effective: false, assist_explanation_effective: false }),
    )
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="assist-flags-steward"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="assist-platform-hint"]').exists()).toBe(true)
  })

  it('平台已开（生效）→ 不显示平台提示', async () => {
    const wrapper = mountPanel()
    await vi.waitFor(() => {
      expect(wrapper.find('[data-test="assist-flags-steward"]').exists()).toBe(true)
    })
    expect(wrapper.find('[data-test="assist-platform-hint"]').exists()).toBe(false)
  })
})
