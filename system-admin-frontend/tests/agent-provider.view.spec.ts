/**
 * Agent Provider 治理视图测试（09-06 治理迁移，design §7）：
 * - 列表渲染（名称/类型/密钥状态/启用，无任何密钥形态字段）；
 * - 注册表单提交 payload（含 secret 只写字段）；
 * - 编辑 PATCH 语义：仅提交变更字段；secret 留空=不提交、勾选清除=空串；
 * - 平台默认保存 payload（未设置的维度显式 null=清除）；
 * - 错误 detail 呈现（allowed_models 白名单载荷结构化展示）；
 * - 空间设置只读排查渲染。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('@/api/agent-provider', () => ({
  listProviders: vi.fn(),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  getPlatformDefaults: vi.fn(),
  putPlatformDefaults: vi.fn(),
  getSpaceProviderSettings: vi.fn(),
}))

import {
  createProvider,
  getPlatformDefaults,
  getSpaceProviderSettings,
  listProviders,
  putPlatformDefaults,
  updateProvider,
} from '@/api/agent-provider'
import { AdminApiError } from '@/api/client'
import type { AdminAgentProviderOut } from '@/types/api'
import AgentProviderAdminView from '@/views/AgentProviderAdminView.vue'

const mockedCreate = vi.mocked(createProvider)
const mockedUpdate = vi.mocked(updateProvider)
const mockedPutDefaults = vi.mocked(putPlatformDefaults)
const mockedList = vi.mocked(listProviders)
const mockedGetDefaults = vi.mocked(getPlatformDefaults)
const mockedLookup = vi.mocked(getSpaceProviderSettings)

function providerRow(overrides: Partial<AdminAgentProviderOut> = {}): AdminAgentProviderOut {
  return {
    id: 3,
    name: 'liu-dada',
    kind: 'openai_compatible',
    api: 'openai-responses',
    base_url: 'https://api.liu-dada.com/v1',
    compat: {},
    context_window: 272000,
    max_tokens: 60000,
    reasoning: true,
    input_modalities: ['text', 'image'],
    thinking_levels: ['low', 'high'],
    has_secret: true,
    allowed_models: ['gpt-5.6-sol'],
    enabled: true,
    created_at: '2026-09-06T00:00:00Z',
    updated_at: '2026-09-06T00:00:00Z',
    ...overrides,
  }
}

function emptyDefaults() {
  return { assistant: null, steward: null, updated_at: null }
}

async function mountView() {
  const wrapper = mount(AgentProviderAdminView)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedList.mockResolvedValue([providerRow()])
  mockedGetDefaults.mockResolvedValue(emptyDefaults())
})

describe('AgentProviderAdminView（三区块治理页）', () => {
  it('没有 Provider 时直接展示配置入口，并提供快捷接入', async () => {
    mockedList.mockResolvedValueOnce([])
    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="provider-empty-cta"]').exists()).toBe(true)
    await wrapper.find('[data-testid="provider-empty-cta"]').trigger('click')
    expect(wrapper.find('[data-testid="provider-form"]').exists()).toBe(true)
    expect(wrapper.find('[aria-labelledby="provider-registry-title"]').classes()).toContain('provider-registry-card')
    // 预设只表达通用协议形态，不点名具体供应商
    expect(wrapper.find('[data-testid="quick-provider-openai-compatible"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="quick-provider-ollama"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pd-open-provider"]').exists()).toBe(true)
    await wrapper.find('[data-testid="provider-form-cancel"]').trigger('click')
    expect(wrapper.find('[aria-labelledby="provider-registry-title"]').classes()).not.toContain('provider-modal-open')
  })

  it('快捷接入：通用预设只设协议与类型，Ollama 预填本机连接信息', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-testid="provider-open-create"]').trigger('click')

    // 通用 OpenAI 兼容预设：不替用户选供应商或模型，只定协议/类型
    await wrapper.find('[data-testid="quick-provider-openai-compatible"]').trigger('click')
    expect((wrapper.find('[data-testid="provider-api"]').element as HTMLSelectElement).value).toBe(
      'openai-responses',
    )
    expect((wrapper.find('[data-testid="provider-name"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.find('[data-testid="provider-base-url"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.find('[data-testid="provider-models"]').element as HTMLInputElement).value).toBe('')

    // Ollama 预设：预填本机地址与 completions 协议
    await wrapper.find('[data-testid="quick-provider-ollama"]').trigger('click')
    expect((wrapper.find('[data-testid="provider-base-url"]').element as HTMLInputElement).value).toBe(
      'http://127.0.0.1:11434/v1',
    )
    expect((wrapper.find('[data-testid="provider-api"]').element as HTMLSelectElement).value).toBe(
      'openai-completions',
    )
  })

  it('Provider 列表渲染：名称/类型/密钥状态，响应无密钥形态字段', async () => {
    const wrapper = await mountView()
    const table = wrapper.find('[data-testid="provider-table"]')
    expect(table.exists()).toBe(true)
    expect(table.text()).toContain('liu-dada')
    expect(table.text()).toContain('openai_compatible')
    expect(table.text()).toContain('已配置')
    expect(wrapper.html()).not.toContain('secret_ciphertext')
  })

  it('注册表单提交 payload：name/kind/base_url/secret/allowed_models', async () => {
    mockedCreate.mockResolvedValueOnce(providerRow())
    const wrapper = await mountView()
    await wrapper.find('[data-testid="provider-open-create"]').trigger('click')

    await wrapper.find('[data-testid="provider-name"]').setValue('new-cloud')
    await wrapper.find('[data-testid="provider-base-url"]').setValue('https://api.new.com/v1')
    await wrapper.find('[data-testid="provider-secret"]').setValue('sk-new-plain')
    await wrapper.find('[data-testid="provider-models"]').setValue('model-a, model-b')
    await wrapper.find('[data-testid="provider-form"]').trigger('submit')
    await flushPromises()

    expect(mockedCreate).toHaveBeenCalledTimes(1)
    expect(mockedCreate).toHaveBeenCalledWith({
      name: 'new-cloud',
      kind: 'openai_compatible',
      api: 'openai-responses',
      base_url: 'https://api.new.com/v1',
      allowed_models: ['model-a', 'model-b'],
      enabled: true,
      secret: 'sk-new-plain',
    })
  })

  it('编辑 PATCH 语义：仅提交变更字段；secret 留空不提交', async () => {
    mockedUpdate.mockResolvedValueOnce(providerRow({ enabled: false }))
    const wrapper = await mountView()
    await wrapper.find('[data-testid="provider-edit-3"]').trigger('click')

    // 只把 enabled 改为停用，secret 留空
    await wrapper.find('[data-testid="provider-enabled"]').setValue('false')
    await wrapper.find('[data-testid="provider-form"]').trigger('submit')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith(3, { enabled: false })
  })

  it('编辑勾选清除密钥：PATCH 载荷 secret=""（清除语义）', async () => {
    mockedUpdate.mockResolvedValueOnce(providerRow({ has_secret: false }))
    const wrapper = await mountView()
    await wrapper.find('[data-testid="provider-edit-3"]').trigger('click')

    await wrapper.find('[data-testid="provider-secret-clear"]').setValue(true)
    await wrapper.find('[data-testid="provider-form"]').trigger('submit')
    await flushPromises()

    expect(mockedUpdate).toHaveBeenCalledWith(3, { secret: '' })
  })

  it('平台默认保存 payload：未设置维度显式 null（清除语义）', async () => {
    mockedPutDefaults.mockResolvedValueOnce({
      assistant: { provider_id: 3, model: 'gpt-5.6-sol' },
      steward: null,
      updated_at: '2026-09-06T01:00:00Z',
    })
    const wrapper = await mountView()

    await wrapper.find('[data-testid="pd-assistant-provider"]').setValue('3')
    // model 下拉联动该 Provider 的 allowed_models
    const modelSelect = wrapper.find('[data-testid="pd-assistant-model"]')
    expect(modelSelect.findAll('option').map((o) => o.element.textContent?.trim())).toEqual(
      expect.arrayContaining(['gpt-5.6-sol']),
    )
    await modelSelect.setValue('gpt-5.6-sol')
    await wrapper.find('[data-testid="pd-save"]').trigger('click')
    await flushPromises()

    expect(mockedPutDefaults).toHaveBeenCalledWith({
      assistant: { provider_id: 3, model: 'gpt-5.6-sol' },
      steward: null,
    })
    expect(wrapper.find('[data-testid="pd-current-assistant"]').text()).toContain('gpt-5.6-sol')
  })

  it('平台默认模型支持直接填写自定义模型 ID', async () => {
    mockedPutDefaults.mockResolvedValueOnce({
      assistant: { provider_id: 3, model: 'private-model-v2' },
      steward: null,
      updated_at: '2026-09-06T01:00:00Z',
    })
    const wrapper = await mountView()
    await wrapper.find('[data-testid="pd-assistant-provider"]').setValue('3')
    await wrapper.find('[data-testid="pd-assistant-model-manual"]').setValue('private-model-v2')
    await wrapper.find('[data-testid="pd-save"]').trigger('click')
    await flushPromises()

    expect(mockedPutDefaults).toHaveBeenCalledWith({
      assistant: { provider_id: 3, model: 'private-model-v2' },
      steward: null,
    })
  })

  it('错误 detail 呈现：allowed_models 白名单载荷结构化展示', async () => {
    mockedUpdate.mockRejectedValueOnce(
      new AdminApiError(422, 'VALIDATION_ERROR', 'model 不在该 Provider 的 allowed_models 内', {
        allowed_models: ['gpt-5.6-sol'],
      }),
    )
    const wrapper = await mountView()
    await wrapper.find('[data-testid="provider-edit-3"]').trigger('click')
    await wrapper.find('[data-testid="provider-enabled"]').setValue('false')
    await wrapper.find('[data-testid="provider-form"]').trigger('submit')
    await flushPromises()

    const error = wrapper.find('[data-testid="provider-form-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('model 不在该 Provider 的 allowed_models 内')
    expect(error.text()).toContain('gpt-5.6-sol')
  })

  it('空间设置只读排查：按维度呈现行级设置与平台默认状态', async () => {
    mockedLookup.mockResolvedValueOnce({
      space_id: 7,
      settings: {
        assistant: {
          agent_kind: 'assistant',
          provider_id: 3,
          model: 'gpt-5.6-sol',
          cloud_allowed: true,
          local_required: false,
          enabled: true,
        },
        steward: null,
      },
      platform_default: emptyDefaults(),
    })
    const wrapper = await mountView()
    await wrapper.find('[data-testid="space-lookup-input"]').setValue('7')
    await wrapper.find('[data-testid="space-lookup-submit"]').trigger('submit')
    await flushPromises()

    const result = wrapper.find('[data-testid="space-lookup-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('gpt-5.6-sol')
    expect(result.text()).toContain('已同意云执行')
    expect(result.text()).toContain('无显式行')
    expect(mockedLookup).toHaveBeenCalledWith(7)
  })
})
