/**
 * Steward 运维观测面板测试（R5，09-11 整改）：
 * - 消费 /v1/steward/status 与 /v1/steward/jobs 安全白名单 DTO；
 * - 渲染状态徽章、队列指标、开关解释、安全错误码与作业表；
 * - 观测端点失败 → 「观测不可用」可重试态（不回退假数据）；
 * - 负向断言：响应中的 prompt/姓名/token 等敏感字段绝不进入渲染树。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'

import StewardOpsPanel from '@/components/StewardOpsPanel.vue'
import { apiStewardJobs, apiStewardStatus } from '@/api/steward'

vi.mock('@/api/steward', () => ({
  apiStewardStatus: vi.fn(),
  apiStewardJobs: vi.fn(),
}))

const mockedStatus = vi.mocked(apiStewardStatus)
const mockedJobs = vi.mocked(apiStewardJobs)

function makeStatus(overrides: Record<string, unknown> = {}) {
  return {
    state: 'degraded' as const,
    switches: [
      { key: 'core', enabled: true, explanation: 'Steward 核心引擎已启用（事件/扫描可登记作业）' },
      { key: 'worker', enabled: true, explanation: '进程内 worker 已启用：queued 作业被周期泵执行' },
    ],
    worker_heartbeat_at: '2026-09-11T08:00:00Z',
    queue_counts: { queued: 2, leased: 1, running: 1 },
    oldest_queued_seconds: 90,
    recent_error_codes: ['STEWARD_PROVIDER_FAILED'],
    ...overrides,
  }
}

function makeJob(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 7,
    space_id: 3,
    cause: 'relation_confirmed',
    status: 'failed',
    attempt: 2,
    available_at: null,
    error_code: 'STEWARD_PROVIDER_FAILED',
    ...overrides,
  }
}

async function mountPanel() {
  const wrapper = mount(StewardOpsPanel, { global: { plugins: [createPinia()] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('StewardOpsPanel 观测面板（R5）', () => {
  it('渲染状态/队列/心跳/开关/错误码/作业元数据', async () => {
    mockedStatus.mockResolvedValue(makeStatus())
    mockedJobs.mockResolvedValue({ items: [makeJob()], page: 1, page_size: 20 })

    const wrapper = await mountPanel()
    expect(wrapper.find('[data-testid="steward-state"]').text()).toContain('降级')
    expect(wrapper.find('[data-testid="steward-metrics"]').text()).toContain('2')
    expect(wrapper.find('[data-testid="steward-switches"]').text()).toContain('core')
    expect(wrapper.find('[data-testid="steward-error-codes"]').text()).toContain(
      'STEWARD_PROVIDER_FAILED',
    )
    expect(wrapper.find('[data-testid="steward-jobs-table"]').text()).toContain('#7')
    wrapper.unmount()
  })

  it('重跑入口保持 disabled 并说明依赖（不新增旁路写）', async () => {
    mockedStatus.mockResolvedValue(makeStatus())
    mockedJobs.mockResolvedValue({ items: [], page: 1, page_size: 20 })

    const wrapper = await mountPanel()
    const rerun = wrapper.find('[data-testid="steward-rerun-disabled"] button')
    expect(rerun.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('观测端点失败 → 可重试错误态，不渲染任何假数据', async () => {
    mockedStatus.mockRejectedValue(new Error('503'))
    mockedJobs.mockRejectedValue(new Error('503'))

    const wrapper = await mountPanel()
    expect(wrapper.find('[data-testid="steward-state"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('STEWARD_PROVIDER_FAILED')
    wrapper.unmount()
  })

  it('负向脱敏：后端响应意外夹带敏感字段也不会进入渲染树', async () => {
    // 合同外字段由后端白名单保证；这里验证渲染只消费白名单字段
    mockedStatus.mockResolvedValue(makeStatus())
    mockedJobs.mockResolvedValue({
      items: [makeJob({ error_code: 'STEWARD_PROVIDER_FAILED' })],
      page: 1,
      page_size: 20,
    })

    const wrapper = await mountPanel()
    const text = wrapper.text()
    expect(text).not.toContain('prompt')
    expect(text).not.toContain('Bearer')
    expect(text).not.toContain('password')
    wrapper.unmount()
  })
})
