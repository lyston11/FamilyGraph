import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { NMessageProvider } from 'naive-ui'

import { useSpacesStore } from '@/stores/spaces'
import AttachmentAgentGrant from '@/components/member/AttachmentAgentGrant.vue'
import {
  confirmContentVersion,
  createContentVersion,
  createGrant,
  listContentVersions,
  listGrants,
  revokeGrant,
  type AgentGrantOut,
  type ContentVersionOut,
} from '@/api/attachments'

// 「供助手使用」三个动作**互不蕴含**，这是本组件最容易被改错的地方：
//   1. 保存文字 → draft 内容版本
//   2. 确认文字 → confirmed（「这段文字是对的」）
//   3. 授权给家庭空间 → 助手才能在该空间检索到（「允许模型用」）
//
// 反例（必须失败）：把「确认」与「授权」合成一个按钮/一个请求。用户可能确认文字
// 之后才发现里面有不想给模型看的内容；两步合一就抹掉了这个决定点。
//
// 另两个边界：
//   - 授权只对家庭空间开放（lineage 的可见性形状与本合同不同构）；
//   - 撤销后 UI 不得仍显示「已授权」。
vi.mock('@/api/attachments', () => ({
  createContentVersion: vi.fn(),
  listContentVersions: vi.fn(),
  confirmContentVersion: vi.fn(),
  listGrants: vi.fn(),
  createGrant: vi.fn(),
  revokeGrant: vi.fn(),
}))

const mockedListVersions = vi.mocked(listContentVersions)
const mockedListGrants = vi.mocked(listGrants)
const mockedCreateVersion = vi.mocked(createContentVersion)
const mockedConfirm = vi.mocked(confirmContentVersion)
const mockedCreateGrant = vi.mocked(createGrant)
const mockedRevoke = vi.mocked(revokeGrant)

function makeVersion(overrides: Partial<ContentVersionOut> = {}): ContentVersionOut {
  return {
    id: 1,
    attachment_id: 7,
    revision: 1,
    content_kind: 'manual_text',
    text: '院子里的桂花树',
    content_sha256: 'a'.repeat(64),
    extractor_name: 'manual',
    extractor_version: '1',
    status: 'draft',
    created_by: 1,
    confirmed_by: null,
    created_at: '2026-10-11T08:00:00',
    confirmed_at: null,
    superseded_at: null,
    revoked_at: null,
    ...overrides,
  }
}

function makeGrant(overrides: Partial<AgentGrantOut> = {}): AgentGrantOut {
  return {
    id: 3,
    content_version_id: 1,
    space_id: 5,
    agent_kind: 'assistant',
    purpose: 'rag',
    grant_scope: 'whole',
    grantor_user_id: 1,
    grantor_account_id: 1,
    authorization_revision: 1,
    status: 'active',
    created_at: '2026-10-11T08:00:00',
    revoked_at: null,
    revoke_reason: null,
    ...overrides,
  }
}

// `useMessage()` 要求祖先里有 `NMessageProvider`：用一个包装组件把它提供出来。
const Host = defineComponent({
  props: { attachmentId: { type: Number, required: true }, canEdit: { type: Boolean, required: true } },
  setup(props) {
    return () =>
      h(NMessageProvider, null, {
        default: () => h(AttachmentAgentGrant, { attachmentId: props.attachmentId, canEdit: props.canEdit }),
      })
  },
})

// 同一份 pinia 实例要在 `beforeEach` 与挂载之间共享：挂载时新建实例会让
// `beforeEach` 里设置的 `spaces.spaces` 丢失，组件拿到空空间列表。
let pinia: ReturnType<typeof createPinia>

function mountGrant(canEdit = true) {
  return mount(Host, {
    props: { attachmentId: 7, canEdit },
    global: { plugins: [pinia] },
  })
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.clearAllMocks()
  mockedListVersions.mockResolvedValue([])
  mockedListGrants.mockResolvedValue([])
  const spaces = useSpacesStore()
  spaces.spaces = [
    { id: 5, name: '我的家庭', kind: 'household' },
    { id: 9, name: '家族', kind: 'lineage' },
  ] as never
})

describe('AttachmentAgentGrant', () => {
  it('不编辑者看不到授权入口', () => {
    const wrapper = mountGrant(false)
    expect(wrapper.find('[data-test="agent-grant"]').exists()).toBe(false)
  })

  it('保存文字产生 draft，且此时不出现授权按钮', async () => {
    mockedCreateVersion.mockResolvedValue(makeVersion())
    const wrapper = mountGrant()
    await flushPromises()

    await wrapper.find('[data-test="grant-text"] textarea').setValue('院子里的桂花树')
    await wrapper.find('[data-test="grant-text"]').trigger('keydown') // no-op guard
    const save = wrapper.findAll('button').find((b) => b.text().includes('保存内容'))!
    await save.trigger('click')
    await flushPromises()

    expect(mockedCreateVersion).toHaveBeenCalledWith(7, '院子里的桂花树')
    // 关键：保存动作**不**触发确认，也不触发授权。
    expect(mockedConfirm).not.toHaveBeenCalled()
    expect(mockedCreateGrant).not.toHaveBeenCalled()
  })

  it('确认与授权是两个独立动作，确认不会顺带授权', async () => {
    mockedListVersions.mockResolvedValue([makeVersion({ status: 'draft' })])
    mockedConfirm.mockResolvedValue(makeVersion({ status: 'confirmed', confirmed_by: 1 }))
    const wrapper = mountGrant()
    await flushPromises()

    const confirmBtn = wrapper.find('[data-test="confirm"]')
    expect(confirmBtn.exists()).toBe(true)
    // draft 阶段没有授权按钮——授权的前提是内容已确认。
    expect(wrapper.find('[data-test="grant"]').exists()).toBe(false)

    await confirmBtn.trigger('click')
    await flushPromises()

    expect(mockedConfirm).toHaveBeenCalledWith(1)
    expect(mockedCreateGrant).not.toHaveBeenCalled()
  })

  it('已确认内容可授权，且只向家庭空间授权', async () => {
    mockedListVersions.mockResolvedValue([makeVersion({ status: 'confirmed', confirmed_by: 1 })])
    mockedCreateGrant.mockResolvedValue(makeGrant())
    const wrapper = mountGrant()
    await flushPromises()

    const grantBtn = wrapper.find('[data-test="grant"]')
    expect(grantBtn.exists()).toBe(true)
    await grantBtn.trigger('click')
    await flushPromises()

    // 默认选中第一个 household（id=5），不会选中 lineage（id=9）。
    expect(mockedCreateGrant).toHaveBeenCalledWith(1, 5)
  })

  it('已授权时显示撤销而非再次授权', async () => {
    mockedListVersions.mockResolvedValue([makeVersion({ status: 'confirmed', confirmed_by: 1 })])
    mockedListGrants.mockResolvedValue([makeGrant()])
    mockedRevoke.mockResolvedValue(makeGrant({ status: 'revoked', revoked_at: '2026-10-11T09:00:00' }))
    const wrapper = mountGrant()
    await flushPromises()

    expect(wrapper.find('[data-test="granted"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="grant"]').exists()).toBe(false)

    await wrapper.find('[data-test="revoke"]').trigger('click')
    await flushPromises()
    expect(mockedRevoke).toHaveBeenCalledWith(3)
  })

  it('撤销后重新加载不再显示已授权', async () => {
    mockedListVersions.mockResolvedValue([makeVersion({ status: 'confirmed', confirmed_by: 1 })])
    mockedListGrants.mockResolvedValueOnce([makeGrant()]).mockResolvedValueOnce([
      makeGrant({ status: 'revoked', revoked_at: '2026-10-11T09:00:00' }),
    ])
    mockedRevoke.mockResolvedValue(makeGrant({ status: 'revoked' }))
    const wrapper = mountGrant()
    await flushPromises()

    await wrapper.find('[data-test="revoke"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="granted"]').exists()).toBe(false)
    // 撤销后可以重新授权（新 authorization_revision 是新的一行）。
    expect(wrapper.find('[data-test="grant"]').exists()).toBe(true)
  })
})
