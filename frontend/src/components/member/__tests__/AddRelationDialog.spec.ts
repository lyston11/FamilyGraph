import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

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

async function mountDialog() {
  const pinia = createPinia()
  const wrapper = mount(AddRelationDialog, {
    props: { visible: true },
    global: { plugins: [pinia, ElementPlus] },
  })
  const auth = useAuthStore(pinia)
  auth.user = { id: 1, name: '我', is_admin: false, pin_must_change: false }
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
  })

  it('搜索排除本人并展示候选', async () => {
    const wrapper = await mountDialog()
    ;(wrapper.vm as unknown as { results: Member[] }).results = [
      makeMember({ id: 2 }),
      makeMember({ id: 1, name: '我' }),
    ]
    await wrapper.vm.$nextTick()
    const items = wrapper.findAll('[data-test="candidate"]')
    expect(items.length).toBe(2)
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

    await wrapper.find('[data-test="candidate"]').trigger('click')

    const radios = wrapper.findAll('[data-test="dir-class-group"] input[type="radio"]')
    expect(radios.length).toBe(4)
    await radios[3].setValue(true) // 配偶
    await radios[0].setValue(true) // 长辈

    await wrapper.find('input[placeholder="选填，如：三叔公"]').setValue('三叔公')

    await wrapper.find('[data-test="submit-relation"]').trigger('click')
    await vi.waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1))
    expect(mockedCreate).toHaveBeenCalledWith({
      target_id: 2,
      dir_class: 'elder',
      label: '三叔公',
    })
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
    await wrapper.find('[data-test="candidate"]').trigger('click')
    await wrapper.find('[data-test="submit-relation"]').trigger('click')
    await vi.waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(wrapper.text()).toContain('等待对方确认'))
    expect(wrapper.find('[data-test="submit-relation"]').exists()).toBe(false)
  })
})
