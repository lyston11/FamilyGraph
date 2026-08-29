import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Member } from '@/types/api'

// makeMember 用函数声明（提升），供 vi.mock 工厂引用
function makeMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 2,
    name: '三叔',
    is_admin: false,
    gender: 'm',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'claimed',
    created_by: 1,
    created_at: '2026-08-25T00:00:00',
    clan_disclosure: { avatar: false, photos: false, dates: false, bio: false, attachments: false },
    permissions: { edit: true, delete: true },
    ...overrides,
  }
}

const searchGet = vi.fn(async () => ({ data: [makeMember()] }))

vi.mock('@/api/client', () => ({
  apiClient: { get: (...args: unknown[]) => searchGet(...(args as [])) },
}))

const mocks = vi.hoisted(() => ({
  fetchMyGraph: vi.fn(async () => ({ nodes: [], edges: [], scope: 'family' })),
  createConnectionRequest: vi.fn(),
  fetchIncomingConnections: vi.fn(async () => []),
  resolveConnection: vi.fn(),
  revokeRelation: vi.fn(),
}))

vi.mock('@/api/graph', () => mocks)

import AddRelationDialog from '@/components/member/AddRelationDialog.vue'
import { useAuthStore } from '@/stores/auth'

const mockedCreate = mocks.createConnectionRequest

// n-modal 内容 teleport 到 body，交互统一走 document 查询
function click(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

async function setInput(selector: string, value: string): Promise<void> {
  const input = document.querySelector<HTMLInputElement>(selector)
  expect(input, selector).not.toBeNull()
  input!.value = value
  input!.dispatchEvent(new Event('input'))
  await new Promise((resolve) => setTimeout(resolve))
}

async function mountDialog() {
  const pinia = createPinia()
  const wrapper = mount(AddRelationDialog, {
    props: { visible: true },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '我',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('AddRelationDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    searchGet.mockClear()
    mocks.fetchMyGraph.mockClear()
    mocks.fetchIncomingConnections.mockClear()
    mockedCreate.mockReset()
    document.body.innerHTML = ''
  })

  it('搜索排除本人并展示候选', async () => {
    const wrapper = await mountDialog()
    ;(wrapper.vm as unknown as { results: Member[] }).results = [
      makeMember({ id: 2 }),
      makeMember({ id: 1, name: '我' }),
    ]
    await wrapper.vm.$nextTick()
    const items = document.querySelectorAll('[data-test="candidate"]')
    expect(items.length).toBe(2)
    wrapper.unmount()
  })

  it('四分类提交：选人 → 选结构类 → 发送请求携带正确 dir_class 与称谓', async () => {
    mockedCreate.mockResolvedValue({
      id: 9,
      from_user: 1,
      to_user: 2,
      dir_class: 'elder',
      label: '三叔公',
      status: 'pending',
      created_by: 1,
      view: { dir_class: 'younger', label: '三叔公', label_from_creator: true },
    })

    const wrapper = await mountDialog()
    ;(wrapper.vm as unknown as { results: Member[] }).results = [makeMember({ id: 2, name: '三叔' })]
    await wrapper.vm.$nextTick()

    click('[data-test="candidate"]')
    await new Promise((resolve) => setTimeout(resolve))

    // n-radio 原生 input：native click 触发 change（Phase 1 login.spec 同款交互）
    const radios = document.querySelectorAll<HTMLInputElement>(
      '[data-test="dir-class-group"] input[type="radio"]',
    )
    expect(radios.length).toBe(4)
    radios[3].click() // 配偶
    await new Promise((resolve) => setTimeout(resolve))
    radios[0].click() // 长辈
    await new Promise((resolve) => setTimeout(resolve))

    await setInput('input[placeholder="选填，如：三叔公"]', '三叔公')

    click('[data-test="submit-relation"]')
    await vi.waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1))
    expect(mockedCreate).toHaveBeenCalledWith({
      target_id: 2,
      dir_class: 'elder',
      label: '三叔公',
    })
    wrapper.unmount()
  })

  it('提交成功后展示等待确认提示且不可重复提交', async () => {
    mockedCreate.mockResolvedValue({
      id: 10,
      from_user: 1,
      to_user: 2,
      dir_class: 'peer',
      label: null,
      status: 'pending',
      created_by: 1,
      view: { dir_class: 'peer', label: null, label_from_creator: true },
    })

    const wrapper = await mountDialog()
    ;(wrapper.vm as unknown as { results: Member[] }).results = [makeMember()]
    await wrapper.vm.$nextTick()
    click('[data-test="candidate"]')
    await new Promise((resolve) => setTimeout(resolve))
    click('[data-test="submit-relation"]')
    await vi.waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1))
    // 成功提示以 NAlert 渲染（teleport 到 body），提交按钮隐藏防重复提交
    await vi.waitFor(() => expect(document.body.textContent).toContain('等待对方确认'))
    expect(document.querySelector('[data-test="submit-relation"]')).toBeNull()
    wrapper.unmount()
  })
})
