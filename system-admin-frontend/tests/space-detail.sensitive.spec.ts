/**
 * 空间详情敏感详情流程测试（PRD FE-F4/FE-F5）：
 * - 点击"查看档案"先弹理由表单，未授权时不加载档案数据；
 * - 提交理由 → POST /v1/access-sessions（user 目标）→ 档案/附件/头像加载；
 * - 档案字段白名单渲染（无 updated_at / avatar_path / 电话邮箱伪造字段）；
 * - 数据请求 403 票据失效 → 清票据 → 重新弹理由表单；
 * - 关系/事实 tab 需要 space 票据。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'
import SpaceDetailView from '@/views/SpaceDetailView.vue'
import { createAdminRouter, setupAdminRouterGuards } from '@/router'
import { useAdminAuthStore } from '@/stores/auth'

const mockedAdminRequest = vi.mocked(adminRequest)

const SPACE = {
  space_id: 1,
  name: '陈氏族谱',
  kind: 'lineage' as const,
  created_at: '2026-08-01T00:00:00Z',
  manager_user_id: 12,
  manager_name: '陈大文',
  member_count: 2,
  anomalies: [],
}

const MEMBERS_PAGE = {
  items: [
    {
      user_id: 21,
      name: '李秀英',
      role: 'member' as const,
      status: 'active',
      created_at: '2026-08-02T00:00:00Z',
      updated_at: '2026-08-02T00:00:00Z',
    },
  ],
  page: 1,
  page_size: 20,
  total: 25,
  has_more: true,
}

const PROFILE = {
  id: 21,
  name: '李秀英',
  gender: 'female',
  birth: { cal_type: 'lunar', date: '1948-03-12', mirror_date: '1948-04-20' },
  death: null,
  bio: '家族长者',
  avatar_available: true,
  profile_status: 'identity_confirmed' as const,
  claim_status: 'claimed' as const,
  created_at: '2026-08-02T00:00:00Z',
}

const ATTACHMENTS_PAGE = {
  items: [
    { id: 3, type: 'image' as const, title_safe: '族谱扫描件', created_at: '2026-08-03T00:00:00Z' },
  ],
  page: 1,
  page_size: 10,
  total: 1,
  has_more: false,
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  mockedAdminRequest.mockReset()
})

/** 先建立登录会话（路由守卫要求），再挂载目标路由。 */
async function loginAndMount(): Promise<ReturnType<typeof mount>> {
  mockedAdminRequest.mockResolvedValueOnce({
    access_token: 'access-1',
    refresh_token: 'refresh-1',
    token_type: 'bearer',
    admin: { id: 1, username: 'admin', password_must_change: false, status: 'claimed' },
  })
  await useAdminAuthStore().login('admin', 'Sup3rSecret!pass')

  const router = createAdminRouter()
  setupAdminRouterGuards(router)
  await router.push('/spaces/1')
  const wrapper = mount(SpaceDetailView, {
    global: { plugins: [router], stubs: { teleport: true } },
  })
  await flushPromises()
  return wrapper
}

/** 面板挂载/错误回流的微任务链较长，多轮 flush。 */
async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await flushPromises()
    await nextTick()
  }
}

describe('SpaceDetailView 敏感详情流程', () => {
  it('查看档案先弹理由表单；提交理由后签发 user 票据并加载档案', async () => {
    // 序列：space detail → members → access-session → profile+attachments+avatar(blob)
    mockedAdminRequest.mockImplementation(async (config) => {
      const url = config['url'] as string
      if (url === '/v1/spaces/1') return SPACE
      if (url === '/v1/spaces/1/members') return MEMBERS_PAGE
      if (url === '/v1/access-sessions') {
        return {
          session_id: 'ticket-user-21',
          target_type: 'user',
          target_id: 21,
          allowed_scopes: ['profile.detail'],
          issued_at: '2026-09-04T00:00:00Z',
          expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
        }
      }
      if (url === '/v1/users/21/profile') return PROFILE
      if (url === '/v1/users/21/attachments') return ATTACHMENTS_PAGE
      if (url === '/v1/users/21/avatar/thumbnail') return new Blob(['png'], { type: 'image/png' })
      throw new Error(`unexpected url ${url}`)
    })

    const wrapper = await loginAndMount()

    // 成员表可见，点击查看档案
    expect(wrapper.find('[data-testid="members-table"]').exists()).toBe(true)
    await wrapper.find('[data-testid="open-profile-button"]').trigger('click')
    await flushPromises()

    // 理由弹窗出现，档案尚未加载
    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    const profileCalls = mockedAdminRequest.mock.calls.filter(
      (call) => (call[0] as Record<string, unknown>)['url'] === '/v1/users/21/profile',
    )
    expect(profileCalls).toHaveLength(0)

    // 空理由被拦截
    await dialog.find('[data-testid="reason-submit"]').trigger('click')
    await flushPromises()
    expect(dialog.find('[role="alert"]').exists()).toBe(true)

    // 提交理由 → access-session + 档案加载
    await dialog.find('#access-reason-input').setValue('处理异常队列工单，核查该成员档案状态')
    await dialog.find('[data-testid="reason-submit"]').trigger('click')
    await settle()

    const calls = mockedAdminRequest.mock.calls.map((call) => call[0] as Record<string, unknown>)
    const accessCall = calls.find((c) => c['url'] === '/v1/access-sessions')
    expect(accessCall).toBeTruthy()
    expect(accessCall?.['data']).toEqual({
      target_type: 'user',
      target_id: 21,
      reason: '处理异常队列工单，核查该成员档案状态',
    })
    expect(wrapper.find('[data-testid="member-profile-panel"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="member-profile-name"]').text()).toContain('李秀英')
    expect(wrapper.find('[data-testid="member-avatar"]').exists()).toBe(true)
    // 字段白名单：不渲染电话/邮箱/地址等不存在字段
    expect(wrapper.find('[data-testid="member-profile-panel"]').text()).not.toContain('电话')
    expect(wrapper.find('[data-testid="member-profile-panel"]').text()).not.toContain('邮箱')
    expect(wrapper.find('[data-testid="member-profile-panel"]').text()).not.toContain('住址')
  })

  it('档案请求 403 票据失效 → 清票据并重新弹理由表单', async () => {
    const { AccessSessionInvalidError } = await import('@/api/client')
    mockedAdminRequest.mockImplementation(async (config) => {
      const url = config['url'] as string
      if (url === '/v1/spaces/1') return SPACE
      if (url === '/v1/spaces/1/members') return MEMBERS_PAGE
      if (url === '/v1/access-sessions') {
        return {
          session_id: 'ticket-user-21',
          target_type: 'user',
          target_id: 21,
          allowed_scopes: [],
          issued_at: '2026-09-04T00:00:00Z',
          expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
        }
      }
      if (url === '/v1/users/21/profile') throw new AccessSessionInvalidError()
      if (url === '/v1/users/21/attachments') throw new AccessSessionInvalidError()
      throw new Error(`unexpected url ${url}`)
    })

    const wrapper = await loginAndMount()
    await wrapper.find('[data-testid="open-profile-button"]').trigger('click')
    await flushPromises()
    let dialog = wrapper.find('[role="dialog"]')
    await dialog.find('#access-reason-input').setValue('核查档案')
    await dialog.find('[data-testid="reason-submit"]').trigger('click')
    await settle()

    // 票据签发成功但数据请求 403：面板关闭，重新弹理由表单
    dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)
    expect(dialog.text()).toContain('重新提交理由')
  })

  it('关系 tab 需要 space 票据：未授权时先弹理由表单', async () => {
    mockedAdminRequest.mockImplementation(async (config) => {
      const url = config['url'] as string
      if (url === '/v1/spaces/1') return SPACE
      if (url === '/v1/spaces/1/members') return MEMBERS_PAGE
      if (url === '/v1/access-sessions') {
        return {
          session_id: 'ticket-space-1',
          target_type: 'space',
          target_id: 1,
          allowed_scopes: ['relation.detail'],
          issued_at: '2026-09-04T00:00:00Z',
          expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
        }
      }
      if (url === '/v1/spaces/1/relations') {
        return { items: [], page: 1, page_size: 20, total: 0, has_more: false }
      }
      throw new Error(`unexpected url ${url}`)
    })

    const wrapper = await loginAndMount()
    await wrapper.find('[data-testid="tab-relations"]').trigger('click')
    await flushPromises()

    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.exists()).toBe(true)

    await dialog.find('#access-reason-input').setValue('核查空间关系异常')
    await dialog.find('[data-testid="reason-submit"]').trigger('click')
    await settle()

    const calls = mockedAdminRequest.mock.calls.map((call) => call[0] as Record<string, unknown>)
    const accessCall = calls.find((c) => c['url'] === '/v1/access-sessions')
    expect(accessCall?.['data']).toEqual({
      target_type: 'space',
      target_id: 1,
      reason: '核查空间关系异常',
    })
    expect(wrapper.find('[data-testid="space-relations-panel"]').exists()).toBe(true)
  })

  it('未知空间 → 统一安全 404 空态', async () => {
    mockedAdminRequest.mockImplementation(async (config) => {
      const url = config['url'] as string
      if (url === '/v1/spaces/1') {
        throw new (await import('@/api/client')).AdminApiError(404, 'ADMIN_RESOURCE_NOT_FOUND', '目标不存在或不可访问')
      }
      throw new Error(`unexpected url ${url}`)
    })
    const wrapper = await loginAndMount()
    expect(wrapper.find('[data-testid="space-not-found"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('陈氏族谱')
  })
})
