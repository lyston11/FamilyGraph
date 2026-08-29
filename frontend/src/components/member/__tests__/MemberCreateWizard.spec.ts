import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import MemberCreateWizard from '@/components/member/MemberCreateWizard.vue'
import { useMembersStore } from '@/stores/members'
import type { FamilySpace, Member } from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  createSpace: vi.fn(),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  inviteToSpace: vi.fn(),
  removeOrWithdrawMembership: vi.fn(),
  resolveMembership: vi.fn(),
  joinByUser: vi.fn(),
  getSpacePositions: vi.fn(),
  putSpacePositions: vi.fn(),
  createOwnershipTransfer: vi.fn(),
  fetchOwnershipTransfers: vi.fn().mockResolvedValue([]),
  respondOwnershipTransfer: vi.fn(),
}))

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

// naive useMessage 需 NMessageProvider 祖先；n-modal 内容 teleport 到 body
const MessageProvidedWizard = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(MemberCreateWizard))])
  },
})

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

/** n-radio 原生 input：native click 触发 change（Phase 1 login.spec 同款交互） */
async function clickRadio(groupSelector: string, index: number): Promise<void> {
  const radios = document.querySelectorAll<HTMLInputElement>(
    `${groupSelector} input[type="radio"]`,
  )
  expect(radios.length, groupSelector).toBeGreaterThan(index)
  radios[index].click()
  await new Promise((resolve) => setTimeout(resolve))
}

const nextButton = (): HTMLButtonElement =>
  document.querySelector<HTMLButtonElement>('[data-test="wizard-next"]')!

async function mountWizard() {
  const pinia = createPinia()
  const wrapper = mount(MessageProvidedWizard, {
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** 第一步通用操作：填名字 + 选关系（F-1 双必填） */
async function fillInfoStep(name = '母亲'): Promise<void> {
  await setInput('[data-test="wizard-name"] input', name)
  await clickRadio('[data-test="wizard-relation-dir"]', 0)
}

describe('MemberCreateWizard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('第一步：名字为空禁用下一步；仅填名字仍禁用（关系必填）；选关系后放行', async () => {
    const wrapper = await mountWizard()

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-next"]')).not.toBeNull(),
    )
    expect(nextButton().disabled).toBe(true)

    await setInput('[data-test="wizard-name"] input', '母亲')
    expect(nextButton().disabled).toBe(true)

    const relationRadios = document.querySelectorAll(
      '[data-test="wizard-relation-dir"] input[type="radio"]',
    )
    expect(relationRadios.length).toBe(4)
    await clickRadio('[data-test="wizard-relation-dir"]', 0)

    expect(nextButton().disabled).toBe(false)
    click('[data-test="wizard-next"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-mode"]')).not.toBeNull(),
    )
    wrapper.unmount()
  })

  it('完整流程（无空间）：资料 → 归属模式 → 确认提交 → 建档 + 关系请求发出 + PIN 弹窗', async () => {
    const pinia = createPinia()
    const wrapper = mount(MessageProvidedWizard, {
      global: { plugins: [pinia] },
      attachTo: document.body,
    })
    const store = useMembersStore(pinia)
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '654321', replayed: false })
    await wrapper.vm.$nextTick()

    await fillInfoStep()
    click('[data-test="wizard-next"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-mode"]')).not.toBeNull(),
    )

    // 归属模式：默认 handover，选择「永久管理」（n-radio 原生 input click）
    const modeRadios = document.querySelectorAll(
      '[data-test="wizard-mode"] input[type="radio"]',
    )
    expect(modeRadios.length).toBe(2)
    ;(modeRadios[1] as HTMLInputElement).click()
    await new Promise((resolve) => setTimeout(resolve))
    expect(store.members).toHaveLength(0)
    click('[data-test="wizard-to-confirm"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-confirm"]')).not.toBeNull(),
    )
    click('[data-test="wizard-submit"]')

    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: '母亲',
          privacy_mode: 'perpetual',
          space_membership: null,
          relation_dir_class: 'elder',
          relation_label: null,
        }),
        expect.any(String),
      ),
    )
    // store 已落新档案（服务端数据唯一来源）
    await vi.waitFor(() => expect(store.members).toHaveLength(1))
    await vi.waitFor(() =>
      expect(wrapper.findComponent(MemberCreateWizard).emitted('created')).toEqual([
        [{ name: '母亲', pin: '654321' }],
      ]),
    )
    wrapper.unmount()
  })

  it('提交失败：展示错误文案、不 emit created、不发关系请求', async () => {
    const { ApiError } = await import('@/api/errors')
    mockedCreate.mockRejectedValue(new ApiError(422, 'VALIDATION_ERROR', '请求参数不合法'))
    const wrapper = await mountWizard()

    await fillInfoStep()
    click('[data-test="wizard-next"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-mode"]')).not.toBeNull(),
    )
    click('[data-test="wizard-to-confirm"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-confirm"]')).not.toBeNull(),
    )
    click('[data-test="wizard-submit"]')

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-error"]')?.textContent).toBe(
        '请求参数不合法',
      ),
    )
    expect(wrapper.findComponent(MemberCreateWizard).emitted('created')).toBeUndefined()
    wrapper.unmount()
  })

  it('空间选择（F-3）：未选具体空间时拦截；新建族谱空间后自动选中并随建档提交引用', async () => {
    vi.mocked(spacesApi.fetchSpaces).mockResolvedValue([
      {
        id: 3,
        name: '我家',
        owner_id: 1,
        kind: 'household',
        created_at: '2026-08-25T00:00:00',
        pending_count: 0,
        member_count: 2,
      },
    ])
    vi.mocked(spacesApi.createSpace).mockResolvedValue({
      id: 7,
      name: '王家族谱',
      owner_id: 1,
      kind: 'lineage',
      created_at: '2026-08-26T00:00:00',
      pending_count: 0,
      member_count: 1,
    } satisfies FamilySpace)
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '111111', replayed: false })
    const wrapper = await mountWizard()

    // 等待 onMounted 的 spaces.load() 完成
    await new Promise((resolve) => setTimeout(resolve))

    await fillInfoStep()
    click('[data-test="wizard-next"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-mode"]')).not.toBeNull(),
    )
    click('[data-test="wizard-to-confirm"]') // 进入空间步骤

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-space"]')).not.toBeNull(),
    )

    // 选「家庭空间」但未选具体空间 → 下一步被拦截
    await clickRadio('[data-test="wizard-space-choice"]', 1)
    click('[data-test="wizard-to-confirm"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-error"]')?.textContent).toContain(
        '请先选择一个具体空间',
      ),
    )

    // 切到族谱空间并内联新建 → 自动选中 → 可进入确认
    await clickRadio('[data-test="wizard-space-choice"]', 2)
    await setInput('[data-test="wizard-lineage-name"] input', '王家族谱')
    click('[data-test="wizard-lineage-create"]')
    await vi.waitFor(() => expect(spacesApi.createSpace).toHaveBeenCalledWith('王家族谱', 'lineage'))

    click('[data-test="wizard-to-confirm"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-confirm"]')).not.toBeNull(),
    )
    click('[data-test="wizard-submit"]')

    await vi.waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({ space_membership: { space_id: 7 } }),
        expect.any(String),
      ),
    )
    wrapper.unmount()
  })
})
