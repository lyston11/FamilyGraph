import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NMessageProvider } from 'naive-ui'

import * as membersApi from '@/api/members'
import * as spacesApi from '@/api/spaces'
import HomeView from '@/views/HomeView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type {
  FamilySpace,
  Member,
  SpaceManagerApplication,
  SpaceMemberInfo,
  SpaceRole,
} from '@/types/api'

vi.mock('@/api/members', () => ({
  fetchMembers: vi.fn(),
  fetchMembersByPrefix: vi.fn(),
  fetchMember: vi.fn(),
  createMember: vi.fn(),
  updateMember: vi.fn(),
  updateDisclosure: vi.fn(),
  removeMember: vi.fn(),
}))

// 空间与图 store 在 onMounted 即请求（mock 掉避免 jsdom 真实 XHR）
vi.mock('@/api/spaces', () => ({
  fetchSpaces: vi.fn().mockResolvedValue([]),
  submitManagerApplication: vi.fn(),
  fetchMyManagerApplications: vi.fn().mockResolvedValue([]),
  fetchSpaceMembers: vi.fn().mockResolvedValue([]),
  fetchSpaceProfileRefs: vi.fn().mockResolvedValue([]),
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

vi.mock('@/api/graph', () => ({
  fetchMyGraph: vi.fn().mockResolvedValue({ nodes: [], edges: [], scope: 'family' }),
  createConnectionRequest: vi.fn(),
  fetchIncomingConnections: vi.fn().mockResolvedValue([]),
  resolveConnection: vi.fn(),
  revokeRelation: vi.fn(),
}))

const mockedFetch = vi.mocked(membersApi.fetchMembers)
const mockedCreate = vi.mocked(membersApi.createMember)
const mockedFetchSpaces = vi.mocked(spacesApi.fetchSpaces)
const mockedFetchSpaceMembers = vi.mocked(spacesApi.fetchSpaceMembers)
const mockedResolveMembership = vi.mocked(spacesApi.resolveMembership)

function makeSpace(overrides: Partial<FamilySpace> = {}): FamilySpace {
  return {
    id: 1,
    name: '我们家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-25T00:00:00',
    pending_count: 0,
    member_count: 2,
    ...overrides,
  }
}

function makeMembership(overrides: Partial<SpaceMemberInfo> = {}): SpaceMemberInfo {
  return {
    id: 99,
    space_id: 1,
    user_id: 5,
    user_name: null,
    added_by: null,
    role: 'member',
    status: 'active',
    updated_at: '2026-08-25T00:00:00',
    ...overrides,
  }
}

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

function makeApplication(overrides: Partial<SpaceManagerApplication> = {}): SpaceManagerApplication {
  return {
    id: 11,
    applicant_user_id: 5,
    applicant_name: '空间用户',
    space_id: 1,
    space_name: '我们家',
    request_kind: 'space_admin',
    status: 'pending',
    decision_note: null,
    created_at: '2026-08-30T00:00:00',
    decided_at: null,
    ...overrides,
  }
}

// naive useMessage 需 NMessageProvider 祖先；div 根保证 test-utils 元素查询稳定
const MessageProvidedHome = defineComponent({
  render() {
    return h('div', [h(NMessageProvider, () => h(HomeView))])
  },
})

async function mountHome(
  role?: SpaceRole,
): Promise<{ wrapper: ReturnType<typeof mount>; router: ReturnType<typeof createRouter> }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore(pinia)
  if (role) {
    mockedFetch.mockResolvedValue([])
    auth.user = {
      id: 5,
      name: '空间用户',
      is_admin: false,
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    }
    mockedFetchSpaces.mockResolvedValue([makeSpace()])
    mockedFetchSpaceMembers.mockResolvedValue([
      makeMembership({ user_id: 5, role, status: 'active' }),
    ])
  } else {
    auth.user = {
      id: 1,
      name: '我',
      is_admin: false,
      pin_must_change: false,
      claim_status: 'claimed',
      profile_status: 'identity_confirmed',
    }
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/home', name: 'home', component: HomeView },
      { path: '/settings', name: 'settings', component: { template: '<div />' } },
    ],
  })
  await router.push('/home')
  await router.isReady()
  const wrapper = mount(MessageProvidedHome, {
    global: {
      plugins: [pinia, router],
    },
    attachTo: document.body,
  })
  await router.isReady()
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, router }
}

// n-modal 内容 teleport 到 body，交互统一走 document 查询
function clickInBody(selector: string): void {
  const target = document.querySelector(selector)
  expect(target, selector).not.toBeNull()
  target!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

async function setInputInBody(selector: string, value: string): Promise<void> {
  const input = document.querySelector<HTMLInputElement>(selector)
  expect(input, selector).not.toBeNull()
  input!.value = value
  input!.dispatchEvent(new Event('input'))
  await new Promise((resolve) => setTimeout(resolve))
}

describe('HomeView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
  })

  it('邀请入口对 active 成员（owner/space_admin/member）显示', async () => {
    for (const role of ['owner', 'space_admin', 'member'] as const) {
      const { wrapper } = await mountHome(role)
      await vi.waitFor(() => expect(wrapper.find('[data-test="invite-member"]').exists()).toBe(true))
      wrapper.unmount()
    }
    mockedFetchSpaces.mockResolvedValue([])
    mockedFetchSpaceMembers.mockResolvedValue([])
  })

  it('guest 不显示邀请入口', async () => {
    const { wrapper } = await mountHome('guest')
    await vi.waitFor(() => expect(mockedFetchSpaceMembers).toHaveBeenCalled())
    expect(wrapper.find('[data-test="invite-member"]').exists()).toBe(false)
    wrapper.unmount()
    mockedFetchSpaces.mockResolvedValue([])
    mockedFetchSpaceMembers.mockResolvedValue([])
  })

  it('家庭空间入口使用 family-space 命名路由', async () => {
    mockedFetch.mockResolvedValue([])
    const { wrapper, router } = await mountHome()

    await wrapper.find('[data-test="go-family-space"]').trigger('click')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('family-space'))
    wrapper.unmount()
  })

  it('空状态给引导动作（添加第一位家人）', async () => {
    mockedFetch.mockResolvedValue([])
    const { wrapper } = await mountHome()

    expect(wrapper.find('[data-test="empty-add"]').exists()).toBe(true)
    expect(mockedFetch).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('渲染与我相关的档案列表并展示确档状态徽章（--fg-status-* 语义）', async () => {
    mockedFetch.mockResolvedValue([
      makeMember({ claim_status: 'claimed' }),
      makeMember({ id: 3, name: '父亲', gender: 'm', claim_status: 'managed' }),
    ])
    const { wrapper } = await mountHome()

    const cards = wrapper.findAll('[data-test="member-card"]')
    expect(cards).toHaveLength(2)
    // v2 身份状态机投影：claimed=已确档（实底）/ managed=待确档（虚线章）
    expect(cards[0].text()).toContain('已确档')
    expect(cards[1].text()).toContain('待确档')
    expect(cards[1].text()).toContain('父亲')
    wrapper.unmount()
  })

  it('建档成功 → 一次性 PIN 弹窗出现；关闭后 PIN 清空不可回看', async () => {
    mockedFetch.mockResolvedValue([makeMember()])
    mockedCreate.mockResolvedValue({ user: makeMember(), pin: '123456', replayed: false })
    const { wrapper } = await mountHome()

    // 打开向导走完三步提交（n-modal teleport 渲染需等待）
    await wrapper.find('[data-test="open-wizard"]').trigger('click')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-name"] input')).not.toBeNull(),
    )
    // v2 F-1：名字与关系必填
    await setInputInBody('[data-test="wizard-name"] input', '母亲')
    const relationRadios = document.querySelectorAll<HTMLInputElement>(
      '[data-test="wizard-relation-dir"] input[type="radio"]',
    )
    expect(relationRadios.length).toBe(4)
    relationRadios[0].click() // 长辈
    await new Promise((resolve) => setTimeout(resolve))
    clickInBody('[data-test="wizard-next"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-mode"]')).not.toBeNull(),
    )
    clickInBody('[data-test="wizard-to-confirm"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="wizard-step-confirm"]')).not.toBeNull(),
    )
    clickInBody('[data-test="wizard-submit"]')

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="one-time-pin"]')?.textContent).toBe('123456'),
    )

    ;(document.querySelector('[data-test="pin-done"]') as HTMLButtonElement).click()
    await new Promise((resolve) => setTimeout(resolve))
    // v-if 卸载 + 父组件清空内存态：PIN 不可回看
    expect(document.querySelector('[data-test="one-time-pin"]')).toBeNull()
    wrapper.unmount()
  })

  it('空间切换器带类型标识（design.md §3.3）：Household=共同生活 / Lineage=谱系', async () => {
    mockedFetchSpaces.mockResolvedValue([
      makeSpace({ id: 1, name: '我们家', kind: 'household' }),
      makeSpace({ id: 2, name: '王家族谱', kind: 'lineage' }),
    ])
    const { wrapper } = await mountHome()

    // load() 自动选中第一个空间（household）→ 触发器 render-label 显示「共同生活」
    const switcher = () => wrapper.find('[data-test="space-switcher"]')
    await vi.waitFor(() => expect(switcher().text()).toContain('共同生活'))
    expect(switcher().text()).toContain('我们家')

    // 切到族谱空间 → 标识随 kind 变化（同一 render-label 通道）
    const spaces = useSpacesStore()
    spaces.currentSpaceId = 2
    await wrapper.vm.$nextTick()
    await vi.waitFor(() => expect(switcher().text()).toContain('谱系'))
    expect(switcher().text()).toContain('王家族谱')
    wrapper.unmount()
  })

  it('邀请行（AD-3）：接受/拒绝分别携带正确的 membership 与 action 调用 resolveMembership', async () => {
    mockedFetchSpaces.mockResolvedValue([makeSpace()])
    mockedFetchSpaceMembers.mockResolvedValue([
      makeMembership({ id: 99, user_id: 5, status: 'pending' }),
    ])
    mockedResolveMembership.mockResolvedValue(makeMembership({ id: 99, status: 'active' }))
    const { wrapper } = await mountHome()

    // 收到的邀请以独立行展示，带接受/拒绝两个动作
    await vi.waitFor(() =>
      expect(wrapper.find('[data-test="space-invite"]').exists()).toBe(true),
    )
    expect(wrapper.find('[data-test="space-invite"]').text()).toContain('邀请你加入')

    await wrapper.find('[data-test="accept-invite"]').trigger('click')
    await vi.waitFor(() => expect(mockedResolveMembership).toHaveBeenCalledWith(99, 'accept'))
    // 成功反馈经 naive message 渲染到 body
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain('已加入家庭空间'),
    )

    await wrapper.find('[data-test="reject-invite"]').trigger('click')
    await vi.waitFor(() => expect(mockedResolveMembership).toHaveBeenCalledWith(99, 'reject'))
    wrapper.unmount()
  })

  it('member 可申请成为当前空间管理员，弹窗确认后提交并展示状态', async () => {
    const mockedSubmit = vi.mocked(spacesApi.submitManagerApplication)
    mockedSubmit.mockResolvedValue(makeApplication())
    const { wrapper } = await mountHome('member')

    const adminEntry = wrapper.find('[data-test="apply-space-admin"]')
    await vi.waitFor(() => expect(adminEntry.exists()).toBe(true))
    await adminEntry.trigger('click')
    await vi.waitFor(() => expect(document.querySelector('[data-test="apply-space-admin-dialog"]')).not.toBeNull())
    await wrapper.findComponent(HomeView).vm.$nextTick()
    clickInBody('[data-test="apply-space-admin-submit"]')

    await vi.waitFor(() => expect(mockedSubmit).toHaveBeenCalledWith('space_admin', { spaceId: 1 }))
    wrapper.unmount()
  })

  it('有空间的 member：可见「申请成为空间管理员」并携带当前空间提交', async () => {
    const mockedSubmit = vi.mocked(spacesApi.submitManagerApplication)
    mockedSubmit.mockResolvedValue(
      makeApplication({ id: 12, request_kind: 'space_admin', space_id: 1, space_name: '我们家' }),
    )
    const { wrapper } = await mountHome('member')

    const adminEntry = wrapper.find('[data-test="apply-space-admin"]')
    await vi.waitFor(() => expect(adminEntry.exists()).toBe(true))
    await adminEntry.trigger('click')
    await vi.waitFor(() => expect(document.querySelector('[data-test="apply-space-admin-dialog"]')).not.toBeNull())
    clickInBody('[data-test="apply-space-admin-submit"]')

    await vi.waitFor(() =>
      expect(mockedSubmit).toHaveBeenCalledWith('space_admin', { spaceId: 1 }),
    )
    wrapper.unmount()
  })

  it('guest 不显示成为管理员入口', async () => {
    const { wrapper } = await mountHome('guest')
    await vi.waitFor(() => expect(mockedFetchSpaceMembers).toHaveBeenCalled())
    expect(wrapper.find('[data-test="apply-space-admin"]').exists()).toBe(false)
    wrapper.unmount()
    mockedFetchSpaces.mockResolvedValue([])
    mockedFetchSpaceMembers.mockResolvedValue([])
  })

  it('我的申请状态行：审批中空心章/未通过朱砂章并展示平台备注', async () => {
    vi.mocked(spacesApi.fetchMyManagerApplications).mockResolvedValue([
      makeApplication({ id: 21 }),
      makeApplication({
        id: 22,
        request_kind: 'space_admin',
        space_id: 1,
        space_name: '我们家',
        status: 'rejected',
        decision_note: '请先完成更多成员邀请',
        decided_at: '2026-08-31T00:00:00',
      }),
    ])
    const { wrapper } = await mountHome('owner')

    const rows = wrapper.findAll('[data-test="manager-application-row"]')
    await vi.waitFor(() => expect(rows.length).toBe(2))
    expect(rows[0].text()).toContain('我们家')
    expect(rows[0].text()).toContain('审批中')
    expect(rows[1].text()).toContain('管理员申请')
    expect(rows[1].text()).toContain('未通过')
    expect(rows[1].text()).toContain('平台备注：请先完成更多成员邀请')
    wrapper.unmount()
  })
})
