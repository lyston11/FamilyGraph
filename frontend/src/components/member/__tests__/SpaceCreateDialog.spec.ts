import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as spacesApi from '@/api/spaces'
import SpaceCreateDialog from '@/components/member/SpaceCreateDialog.vue'
import type { FamilySpace } from '@/types/api'

/**
 * 创建空间弹窗（自旧 HomeView 保全的流程组件）：仅由显式入口打开，
 * 提交走 spaces store → POST /spaces；成功后 emit created。
 */

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  respondOwnershipTransfer: vi.fn(),
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchEligibleManagerTargets: vi.fn().mockResolvedValue([]),
  fetchMyTransferConsents: vi.fn().mockResolvedValue([]),
  respondTransferConsent: vi.fn(),
}))

const mockedCreateSpace = vi.mocked(spacesApi.createSpace)

function makeSpace(id: number, kind: FamilySpace['kind']): FamilySpace {
  return {
    id,
    name: '新空间',
    owner_id: 1,
    kind,
    created_at: '2026-09-01T00:00:00',
    pending_count: 0,
    member_count: 1,
  }
}

const Host = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(SpaceCreateDialog, { visible: true }))])
  },
})

async function mountDialog() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, { global: { plugins: [pinia] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

async function typeName(value: string): Promise<void> {
  const input = document.querySelector<HTMLInputElement>('[data-test="space-name-input"]')
  expect(input).not.toBeNull()
  input!.value = value
  input!.dispatchEvent(new Event('input'))
  await flushPromises()
}

describe('SpaceCreateDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('空名时不发请求；提交创建 household 空间并 emit created', async () => {
    mockedCreateSpace.mockResolvedValue(makeSpace(11, 'household'))
    const wrapper = await mountDialog()

    const submit = document.querySelector<HTMLButtonElement>('[data-test="space-create-submit"]')
    expect(submit?.disabled).toBe(true)
    submit?.click()
    await flushPromises()
    expect(mockedCreateSpace).not.toHaveBeenCalled()

    await typeName('我们家')
    clickSubmitInBody()
    await vi.waitFor(() => expect(mockedCreateSpace).toHaveBeenCalledWith('我们家', 'household'))
    await flushPromises()
    const dialog = wrapper.findComponent(SpaceCreateDialog)
    expect(dialog.emitted('created')).toEqual([
      [expect.objectContaining({ id: 11, kind: 'household' })],
    ])
    wrapper.unmount()
  })

  it('创建失败保留弹窗并提示，不静默成功', async () => {
    mockedCreateSpace.mockRejectedValue(new Error('boom'))
    const wrapper = await mountDialog()
    await typeName('我们家')
    clickSubmitInBody()
    await vi.waitFor(() => expect(document.body.textContent).toContain('创建空间失败'))
    // 弹窗仍在（不假装成功）
    expect(document.querySelector('[data-test="space-create-dialog"]')).not.toBeNull()
    wrapper.unmount()
  })
})

function clickSubmitInBody(): void {
  const target = document.querySelector('[data-test="space-create-submit"]')
  expect(target).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}
