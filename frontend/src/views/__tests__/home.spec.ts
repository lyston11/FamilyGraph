import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import * as membersApi from '@/api/members'
import HomeView from '@/views/HomeView.vue'
import type { Member } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

const mockedFetch = vi.mocked(membersApi.fetchMembers)
const mockedCreate = vi.mocked(membersApi.createMember)

function makeMember(overrides: Partial<Member> = {}): Member {
  return {
    id: 2,
    name: '母亲',
    is_admin: false,
    gender: 'f',
    birth: null,
    death: null,
    bio: null,
    avatar_path: null,
    privacy_mode: 'handover',
    claim_status: 'managed',
    created_by: 1,
    created_at: '2026-08-25T00:00:00',
    clan_disclosure: {
      avatar: false,
      photos: false,
      dates: false,
      bio: false,
      attachments: false,
    },
    permissions: { edit: true, delete: true },
    ...overrides,
  }
}

async function mountHome(): Promise<ReturnType<typeof mount>> {
  const pinia = createPinia()
  const router = {
    push: vi.fn(),
  } as unknown as import('vue-router').Router
  const wrapper = mount(HomeView, {
    global: {
      plugins: [pinia, ElementPlus],
      mocks: { $router: router, $route: { path: '/' } },
    },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return wrapper
}

describe('HomeView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('空状态给引导动作（添加第一位家人）', async () => {
    mockedFetch.mockResolvedValue([])
    const wrapper = await mountHome()

    expect(wrapper.find('[data-test="empty-add"]').exists()).toBe(true)
    expect(mockedFetch).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('渲染与我相关的档案列表并展示认领状态', async () => {
    mockedFetch.mockResolvedValue([
      makeMember({ claim_status: 'claimed' }),
      makeMember({ id: 3, name: '父亲', gender: 'm', claim_status: 'managed' }),
    ])
    const wrapper = await mountHome()

    const cards = wrapper.findAll('[data-test="member-card"]')
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain('已认领')
    expect(cards[1].text()).toContain('待认领')
    expect(cards[1].text()).toContain('父亲')
    wrapper.unmount()
  })

  it('建档成功 → 一次性 PIN 弹窗出现；关闭后 PIN 清空不可回看', async () => {
    mockedFetch.mockResolvedValue([makeMember()])
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '123456' })
    const wrapper = await mountHome()

    // 打开向导走完三步提交（弹窗内容渲染需等待一个 tick）
    await wrapper.find('[data-test="open-wizard"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    expect(wrapper.find('[data-test="wizard-name"]').exists()).toBe(true)
    // v2 F-1：名字与关系必填
    await wrapper.find('[data-test="wizard-name"]').setValue('母亲')
    const relationRadios = wrapper
      .find('[data-test="wizard-relation-dir"]')
      .findAll('.el-radio')
    relationRadios[0].find('.el-radio__original').setValue(true)
    await new Promise((resolve) => setTimeout(resolve))
    await wrapper.find('[data-test="wizard-next"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    await wrapper.find('[data-test="wizard-to-confirm"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve))
    await wrapper.find('[data-test="wizard-submit"]').trigger('click')

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="one-time-pin"]')?.textContent).toBe('123456'),
    )

    ;(document.querySelector('[data-test="pin-done"]') as HTMLButtonElement).click()
    await new Promise((resolve) => setTimeout(resolve))
    // v-if 卸载 + 父组件清空内存态：PIN 不可回看
    expect(document.querySelector('[data-test="one-time-pin"]')).toBeNull()
    wrapper.unmount()
  })
})
