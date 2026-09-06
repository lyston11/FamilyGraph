import { mount } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CLIENT_AGENT_ERRORS } from '@/api/agent'
import ErrorNotice from '@/components/agent/ErrorNotice.vue'
import type { AgentErrorView } from '@/stores/agent'
import { useAuthStore } from '@/stores/auth'
import { useSpacesStore } from '@/stores/spaces'
import type { FamilySpace, SpaceMemberInfo } from '@/types/api'

/**
 * ErrorNotice（09-06 R4）：错误横幅按 AgentErrorView.action 渲染可行动入口。
 * 「去模型设置」仅当前空间管理员可见，点击直达
 * /spaces/{id}/manage?section=models；非管理员保持纯文案（不透传 detail）。
 */

const CONSENT_COPY = '该模型需要云端执行同意，请到 空间管理 → 模型设置 开启'

function consentError(overrides: Partial<AgentErrorView> = {}): AgentErrorView {
  return {
    code: 'PROVIDER_UNRESOLVED',
    message: CONSENT_COPY,
    action: { kind: 'open-model-settings' },
    ...overrides,
  }
}

function makeSpace(): FamilySpace {
  return {
    id: 7,
    name: '王家空间',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-29T00:00:00',
    pending_count: 0,
    member_count: 1,
  }
}

function makeMember(role: SpaceMemberInfo['role']): SpaceMemberInfo {
  return {
    id: 1,
    space_id: 7,
    user_id: 1,
    user_name: '空间用户',
    added_by: 1,
    role,
    status: 'active',
    updated_at: '2026-08-29T00:00:00',
  }
}

let pinia: Pinia
let router: Router

async function mountNotice(
  error: AgentErrorView | null,
  role: SpaceMemberInfo['role'] = 'space_admin',
) {
  const auth = useAuthStore(pinia)
  auth.user = {
    id: 1,
    name: '空间用户',
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
  const spaces = useSpacesStore(pinia)
  spaces.spaces = [makeSpace()]
  spaces.currentSpaceId = 7
  spaces.members = [makeMember(role)]
  const Harness = defineComponent({ render: () => h(ErrorNotice, { error }) })
  return mount(Harness, { global: { plugins: [pinia, router] }, attachTo: document.body })
}

beforeEach(async () => {
  pinia = createPinia()
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'family-space', component: { template: '<div />' } },
      { path: '/spaces/:spaceId/manage', name: 'space-management', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  document.body.innerHTML = ''
})

describe('ErrorNotice（结构化错误横幅与去模型设置入口，09-06 R4）', () => {
  it('管理员 + open-model-settings 动作 → 显示入口，点击直达模型设置深链', async () => {
    const wrapper = await mountNotice(consentError())
    const jump = wrapper.find('[data-test="error-open-model-settings"]')
    expect(jump.exists()).toBe(true)
    await jump.trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.fullPath).toBe('/spaces/7/manage?section=models')
    })
  })

  it('非管理员 + 同样的动作 → 保持纯文案，不显示入口', async () => {
    const wrapper = await mountNotice(consentError(), 'member')
    expect(wrapper.text()).toContain(CONSENT_COPY)
    expect(wrapper.find('[data-test="error-open-model-settings"]').exists()).toBe(false)
  })

  it('管理员但无结构化动作 → 不显示入口', async () => {
    const wrapper = await mountNotice(consentError({ action: undefined }))
    expect(wrapper.find('[data-test="error-open-model-settings"]').exists()).toBe(false)
  })

  it('STREAM_LOST 走文案回退并保留重试入口', async () => {
    const wrapper = await mountNotice({ code: CLIENT_AGENT_ERRORS.STREAM_LOST, message: '' })
    expect(wrapper.text()).toContain('连接中断，任务状态未知，请点击「重试」恢复')
    expect(wrapper.find('[data-test="error-retry"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="error-open-model-settings"]').exists()).toBe(false)
  })

  it('error 为 null 时不渲染', async () => {
    const wrapper = await mountNotice(null)
    expect(wrapper.find('[data-test="error-notice"]').exists()).toBe(false)
  })
})
