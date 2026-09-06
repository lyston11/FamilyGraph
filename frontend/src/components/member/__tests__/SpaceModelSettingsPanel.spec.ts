import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'
import { defineComponent, h } from 'vue'

import {
  fetchSpaceModelSettings,
  updateSpaceModelSetting,
} from '@/api/spaceModelSettings'
import SpaceModelSettingsPanel from '@/components/member/SpaceModelSettingsPanel.vue'
import type { SpaceModelSettings } from '@/types/agent'

vi.mock('@/api/spaceModelSettings', () => ({
  fetchSpaceModelSettings: vi.fn(),
  updateSpaceModelSetting: vi.fn().mockResolvedValue({}),
  resetSpaceModelSetting: vi.fn().mockResolvedValue(undefined),
}))

const mockedFetch = vi.mocked(fetchSpaceModelSettings)
const mockedUpdate = vi.mocked(updateSpaceModelSetting)

function settingsFixture(): SpaceModelSettings {
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
      },
    },
    catalog: [
      {
        provider_id: 3,
        name: 'liu-dada',
        kind: 'openai_compatible',
        api: 'openai-responses',
        models: ['gpt-5.6-sol'],
      },
    ],
    platform_default: { assistant: null, steward: null, updated_at: null },
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
    expect(switches.length).toBe(3)
    // 候选开、排序关、解释关（初值来自行级设置；naive-ui 用 n-switch--active 表达开态）
    const states = switches.map((node) => node.classes().includes('n-switch--active'))
    expect(states).toEqual([true, false, false])

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
  })
})
