/**
 * 运营治理视图测试：审批唯一写 UI（PRD FE-F5）。
 * - approve：理由可选；confirm 弹窗携带不可逆提示；调用 confirm:true；
 * - reject：理由必填（为空时确认按钮禁用）；调用 confirm:true + note；
 * - 成功后展示终态不可改判提示并刷新列表；
 * - 静态断言：页面源码无 PIN 重置/档案修改/删除/导出/下载入口。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

vi.mock('@/api/governance', () => ({
  apiApproveManagerApplication: vi.fn(),
  apiRejectManagerApplication: vi.fn(),
}))

import { adminRequest } from '@/api/client'
import {
  apiApproveManagerApplication,
  apiRejectManagerApplication,
} from '@/api/governance'
import OperationsView from '@/views/OperationsView.vue'
import { createAdminRouter, setupAdminRouterGuards } from '@/router'

const mockedAdminRequest = vi.mocked(adminRequest)
const mockedApprove = vi.mocked(apiApproveManagerApplication)
const mockedReject = vi.mocked(apiRejectManagerApplication)

function queueItem(status: 'pending' | 'approved' | 'rejected', id = 42) {
  return {
    kind: 'manager_application' as const,
    status,
    reference_id: id,
    space_id: 7,
    space_name: '陈氏族谱',
    space_kind: 'lineage' as const,
    anomaly: null,
    applicant_user_id: 12,
    applicant_name: '陈大文',
    request_kind: 'space_admin',
    created_at: '2026-09-04T00:00:00Z',
  }
}

function pageOf(items: unknown[]): unknown {
  return { items, page: 1, page_size: 20, total: items.length, has_more: false }
}

function decided(id: number, status: 'approved' | 'rejected') {
  return {
    id,
    applicant_user_id: 12,
    applicant_name: '陈大文',
    space_id: 7,
    space_name: '陈氏族谱',
    space_kind: 'lineage' as const,
    request_kind: 'space_admin' as const,
    status,
    decision_note: null,
    transfer_consent_id: null,
    transfer_consent_status: null,
    created_at: '2026-09-04T00:00:00Z',
    decided_at: '2026-09-04T01:00:00Z',
    system_admin_decided_by: 1,
  }
}

async function mountView() {
  const router = createAdminRouter()
  setupAdminRouterGuards(router)
  await router.push('/operations')
  const wrapper = mount(OperationsView, {
    global: { plugins: [router], stubs: { teleport: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedAdminRequest.mockReset().mockResolvedValue(pageOf([queueItem('pending')]))
  mockedApprove.mockReset()
  mockedReject.mockReset()
})

describe('OperationsView（审批唯一写 UI）', () => {
  it('待裁决申请展示批准/驳回按钮', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="approve-42"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="reject-42"]').exists()).toBe(true)
  })

  it('批准走二次确认弹窗，confirm 载荷正确且理由可选', async () => {
    mockedApprove.mockResolvedValueOnce(decided(42, 'approved'))
    const wrapper = await mountView()

    await wrapper.find('[data-testid="approve-42"]').trigger('click')
    const modal = wrapper.find('[role="dialog"]')
    expect(modal.exists()).toBe(true)
    // 不可逆提示
    expect(modal.text()).toContain('不可逆')

    await modal.find('[data-testid="decision-confirm"]').trigger('click')
    await flushPromises()

    expect(mockedApprove).toHaveBeenCalledWith(42, null)
    expect(wrapper.find('[data-testid="decision-result"]').text()).toContain('终态')
  })

  it('驳回理由必填：为空时确认禁用；填写后载荷携带理由', async () => {
    mockedReject.mockResolvedValueOnce(decided(42, 'rejected'))
    const wrapper = await mountView()

    await wrapper.find('[data-testid="reject-42"]').trigger('click')
    const modal = wrapper.find('[role="dialog"]')
    expect((modal.find('[data-testid="decision-confirm"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )

    await modal.find('#decision-note-input').setValue('申请人未完成身份确认')
    expect(
      (modal.find('[data-testid="decision-confirm"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
    await modal.find('[data-testid="decision-confirm"]').trigger('click')
    await flushPromises()

    expect(mockedReject).toHaveBeenCalledWith(42, '申请人未完成身份确认')
  })

  it('终态重复裁决 409：弹窗内显示服务端安全文案', async () => {
    const { AdminApiError } = await import('@/api/client')
    mockedReject.mockRejectedValueOnce(
      new AdminApiError(409, 'SPACE_MANAGER_APPLICATION_DECIDED', '该申请已裁决，终态不可改判'),
    )
    const wrapper = await mountView()
    await wrapper.find('[data-testid="reject-42"]').trigger('click')
    const modal = wrapper.find('[role="dialog"]')
    await modal.find('#decision-note-input').setValue('理由')
    await modal.find('[data-testid="decision-confirm"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="decision-error"]').text()).toContain('终态不可改判')
  })

  it('源码静态断言：无 PIN 重置/档案修改/删除/恢复/导出/下载入口', () => {
    const stripComments = (source: string): string =>
      source
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/(^|[^:"'`])\/\/[^\n]*/g, '$1')
    const viewSource = stripComments(readFileSync(join(process.cwd(), 'src/views/OperationsView.vue'), 'utf8'))
    const shellSource = stripComments(readFileSync(join(process.cwd(), 'src/components/AdminShell.vue'), 'utf8'))
    const forbidden = ['重置 PIN', '重置PIN', 'PIN 重置', '修改档案', '删除', '恢复档案', '导出', '下载']
    for (const [name, source] of [
      ['OperationsView', viewSource],
      ['AdminShell', shellSource],
    ] as const) {
      for (const word of forbidden) {
        expect(source, `${name} 不得出现高风险操作入口：${word}`).not.toContain(word)
      }
    }
  })
})
