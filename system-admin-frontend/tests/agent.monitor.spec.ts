/**
 * Agent 监控视图测试（PRD FE-F6 / design §6）：
 * - 5 秒轮询 runs + jobs；
 * - document.visibilityState !== 'visible' 时暂停轮询；
 * - 组件卸载清 timer 并中止 in-flight 请求（不再发起新请求）；
 * - 只渲染二次脱敏诊断四字段。
 *
 * 注意：fake timers 下不能用 VTU 的 flushPromises（内部 setTimeout 会死锁），
 * 这里用微任务循环刷新。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/read', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/read')>()
  return {
    ...actual,
    apiAgentRuns: vi.fn(),
    apiAgentJobs: vi.fn(),
  }
})

import { apiAgentJobs, apiAgentRuns } from '@/api/read'
import AgentMonitorView from '@/views/AgentMonitorView.vue'

const mockedRuns = vi.mocked(apiAgentRuns)
const mockedJobs = vi.mocked(apiAgentJobs)

async function flushMicrotasks(): Promise<void> {
  for (let i = 0; i < 12; i += 1) {
    await Promise.resolve()
  }
  await nextTick()
}

function runsPage() {
  return {
    items: [
      {
        id: 1,
        session_id: 11,
        job_id: null,
        space_id: 3,
        account_id: 21,
        kind: 'assistant',
        status: 'running',
        attempt: 1,
        max_attempts: 3,
        lease_expires_at: null,
        heartbeat_at: null,
        cancel_requested: false,
        error_code: null,
        error: {
          error_code: 'AGENT_TOOL_SCOPE_DENIED',
          component: 'tool.executor',
          stack_location: 'runtime/executor.py:120',
          summary: '工具调用被策略拒绝',
        },
        created_at: '2026-09-04T00:00:00Z',
        updated_at: '2026-09-04T00:01:00Z',
        settled_at: null,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
    has_more: false,
  }
}

function jobsPage() {
  return {
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
    has_more: false,
  }
}

function setVisibility(value: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => value,
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  mockedRuns.mockReset().mockResolvedValue(runsPage() as never)
  mockedJobs.mockReset().mockResolvedValue(jobsPage() as never)
  setVisibility('visible')
})

const mountedWrappers: ReturnType<typeof mount>[] = []

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) {
    wrapper.unmount()
  }
  vi.useRealTimers()
  setVisibility('visible')
})

/** mount 并登记，afterEach 统一卸载（防止 visibilitychange 监听器跨用例泄漏）。 */
function mountTracked(component: typeof AgentMonitorView): ReturnType<typeof mount> {
  const wrapper = mount(component)
  mountedWrappers.push(wrapper)
  return wrapper
}

describe('AgentMonitorView 轮询纪律', () => {
  it('挂载即加载，之后每 5 秒轮询一次', async () => {
    const wrapper = mountTracked(AgentMonitorView)
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(1)
    expect(mockedJobs).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(5000)
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(5000)
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(3)
    expect(wrapper.find('[data-testid="agent-runs-table"]').exists()).toBe(true)
  })

  it('页面不可见时暂停轮询，恢复可见立即刷新', async () => {
    mountTracked(AgentMonitorView)
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(15000)
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(1) // 暂停：无新增请求

    setVisibility('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushMicrotasks()
    expect(mockedRuns).toHaveBeenCalledTimes(2)
  })

  it('组件卸载清除 timer 与 in-flight 请求，不再轮询', async () => {
    const deferred: { resolve?: (value: ReturnType<typeof runsPage>) => void } = {}
    mockedRuns.mockReset().mockImplementation(
      () =>
        new Promise((resolve) => {
          deferred.resolve = resolve
        }),
    )
    const wrapper = mountTracked(AgentMonitorView)
    // 第一轮请求 in-flight（未 resolve）
    wrapper.unmount()
    // 卸载中止 in-flight：请求 promise 稍后 resolve 也不得写状态/继续轮询
    deferred.resolve?.(runsPage())
    await flushMicrotasks()
    await vi.advanceTimersByTimeAsync(15000)
    await flushMicrotasks()
    const callsAfterUnmount = mockedRuns.mock.calls.length
    await vi.advanceTimersByTimeAsync(15000)
    await flushMicrotasks()
    expect(mockedRuns.mock.calls.length).toBe(callsAfterUnmount)
    expect(mockedRuns).toHaveBeenCalledTimes(1)
  })

  it('诊断只渲染脱敏四字段，不渲染原始 error_json/message/prompt', async () => {
    const wrapper = mountTracked(AgentMonitorView)
    await flushMicrotasks()
    const diagnostics = wrapper.find('[data-testid="agent-run-diagnostics"]').text()
    expect(diagnostics).toContain('AGENT_TOOL_SCOPE_DENIED')
    expect(diagnostics).toContain('tool.executor')
    expect(diagnostics).toContain('runtime/executor.py:120')
    expect(diagnostics).toContain('工具调用被策略拒绝')
    const tableText = wrapper.text()
    expect(tableText.toLowerCase()).not.toContain('prompt')
    expect(tableText).not.toContain('error_json')
    expect(tableText).not.toContain('Bearer')
  })
})
