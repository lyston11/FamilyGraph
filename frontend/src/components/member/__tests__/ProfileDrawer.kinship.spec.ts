import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { ElMessage } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ElementPlus from 'element-plus'

import * as kinshipApi from '@/api/kinship'
import { ApiError } from '@/api/errors'
import ProfileDrawer from '@/components/member/ProfileDrawer.vue'
import { useAuthStore } from '@/stores/auth'
import { useMembersStore } from '@/stores/members'
import { useSpacesStore } from '@/stores/spaces'
import type { KinshipResolve } from '@/types/kinship'
import type { Member, UserOut } from '@/types/api'

/**
 * ProfileDrawer「称谓」区合同（V2.3 KI-5 / AC-KI6）：
 * - 展示当前生效称谓 + 来源级别徽章 + 替代称谓；
 * - 个人纠正走 PUT /terms/my，成功后立即强制刷新解析（旧称谓不得残留）；
 * - 「我就这么叫」固定 manual_select，并 toast 晋升状态；
 * - flag 关闭（503）→ 整区隐藏；from_user_id 恒为登录者。
 */

vi.mock('@/api/kinship', () => ({
  KINSHIP_FLAG_DISABLED: 'KINSHIP_FLAG_DISABLED',
  fetchMyTerms: vi.fn().mockResolvedValue([]),
  updateMyTerm: vi.fn(),
  resolveKinship: vi.fn(),
  recordTermUsage: vi.fn(),
  parseRelationText: vi.fn(),
}))

const mockedResolve = vi.mocked(kinshipApi.resolveKinship)
const mockedUpdateMyTerm = vi.mocked(kinshipApi.updateMyTerm)
const mockedUsage = vi.mocked(kinshipApi.recordTermUsage)

function makeMember(): Member {
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
    clan_disclosure: { avatar: false, photos: false, dates: false, bio: false, attachments: false },
    permissions: { edit: true, delete: true },
  }
}

function makeViewer(): UserOut {
  return {
    id: 1,
    name: '我',
    is_admin: false,
    pin_must_change: false,
    claim_status: 'claimed',
    profile_status: 'identity_confirmed',
  }
}

function makeResolve(overrides: Partial<KinshipResolve> = {}): KinshipResolve {
  return {
    found: true,
    viewer_user_id: 1,
    target_user_id: 2,
    space_id: 10,
    path_class: 'parent_child',
    concept_code: 'F_PARENT',
    explanation_structural: '她是你的母亲',
    term: '妈妈',
    term_source_level: 'personal',
    term_entry_id: 5,
    main_path: [],
    alt_paths: [
      { path: [], description: null, concept_code: 'F_PARENT', term: '老妈' },
      { path: [], description: null, concept_code: 'F_PARENT', term: '妈妈' }, // 与主称谓重复，应去重
      { path: [], description: null, concept_code: 'F_PARENT', term: '母亲' },
    ],
    fact_state: { confirmed: 1, proposed: 0, disputed: 0, revoked: 0, evidence_fact_ids: [] },
    cache_hit: false,
    algorithm_version: 'v1',
    ...overrides,
  }
}

// el-dialog teleport 到 body，交互统一走 document
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

async function mountDrawer() {
  const pinia = createPinia()
  const members = useMembersStore(pinia)
  const member = makeMember()
  members.members.push(member)

  const auth = useAuthStore(pinia)
  auth.user = makeViewer()

  const spaces = useSpacesStore(pinia)
  spaces.spaces.push({
    id: 10,
    name: '我的家',
    owner_id: 1,
    kind: 'household',
    created_at: '2026-08-26T00:00:00',
    pending_count: 0,
    member_count: 2,
  })
  spaces.currentSpaceId = 10

  const wrapper = mount(ProfileDrawer, {
    props: { memberId: member.id },
    global: { plugins: [pinia, ElementPlus] },
    attachTo: document.body,
  })
  await new Promise((resolve) => setTimeout(resolve))
  return { wrapper, member }
}

describe('ProfileDrawer 称谓区（V2.3 Block E4c）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(ElMessage, 'success').mockImplementation(() => ({}) as ReturnType<typeof ElMessage.success>)
    vi.spyOn(ElMessage, 'info').mockImplementation(() => ({}) as ReturnType<typeof ElMessage.info>)
    document.body.innerHTML = ''
  })

  it('展示当前生效称谓、来源级别徽章与去重后的替代称谓', async () => {
    mockedResolve.mockResolvedValue(makeResolve())
    const { wrapper } = await mountDrawer()

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-term"]')?.textContent).toContain('妈妈'),
    )
    expect(document.querySelector('[data-test="kinship-term-level"]')?.textContent).toContain('个人称谓')
    // resolve 以登录者身份发起（from_user_id 固定本人）
    expect(mockedResolve).toHaveBeenCalledWith(10, 1, 2)
    const alts = [...document.querySelectorAll('[data-test="kinship-alt-term"]')]
    expect(alts.map((el) => el.textContent?.trim())).toEqual(['老妈', '母亲'])
    wrapper.unmount()
  })

  it('个人纠正：保存后调 PUT terms/my 并立即强制刷新解析（AC-KI6 即时生效）', async () => {
    mockedResolve.mockResolvedValue(makeResolve())
    mockedUpdateMyTerm.mockResolvedValue({
      entry_id: 9,
      concept_code: 'F_PARENT',
      term: '老妈',
      revision: 2,
      updated_at: '2026-08-26T01:00:00',
    })
    const { wrapper } = await mountDrawer()
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-term"]')?.textContent).toContain('妈妈'),
    )

    click('[data-test="kinship-correct-btn"]')
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-correction-dialog"]')).not.toBeNull(),
    )
    await setInput('[data-test="kinship-correction-input"]', '老妈')
    click('[data-test="kinship-correction-save"]')

    await vi.waitFor(() =>
      expect(mockedUpdateMyTerm).toHaveBeenCalledWith({ spaceId: 10, conceptCode: 'F_PARENT', term: '老妈' }),
    )
    // 纠正成功后 force 重算：resolve 调用数从 1 → 2
    await vi.waitFor(() => expect(mockedResolve).toHaveBeenCalledTimes(2))
    expect(ElMessage.success).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('我就这么叫：manual_select 记录使用证据并 toast 晋升状态', async () => {
    mockedResolve.mockResolvedValue(makeResolve())
    mockedUsage.mockResolvedValue({
      usage_id: 1,
      entry_id: 7,
      created: true,
      promotion: { promoted: true, demoted: false, eligible_accounts: 2 },
    })
    const { wrapper } = await mountDrawer()
    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-term"]')?.textContent).toContain('妈妈'),
    )

    click('[data-test="kinship-call-btn"]')
    await vi.waitFor(() =>
      expect(mockedUsage).toHaveBeenCalledWith({
        spaceId: 10,
        conceptCode: 'F_PARENT',
        term: '妈妈',
        sourceEvent: 'manual_select',
      }),
    )
    await vi.waitFor(() =>
      expect(ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('推荐叫法')),
    )
    wrapper.unmount()
  })

  it('flag 关闭（resolve 503）：整个称谓区隐藏，纠正/叫法入口不可见', async () => {
    mockedResolve.mockRejectedValue(new ApiError(503, 'KINSHIP_FLAG_DISABLED', '关系智能能力未启用'))
    const { wrapper } = await mountDrawer()

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-section"]')).toBeNull(),
    )
    expect(document.querySelector('[data-test="kinship-correct-btn"]')).toBeNull()
    expect(document.querySelector('[data-test="kinship-call-btn"]')).toBeNull()
    wrapper.unmount()
  })

  it('found=false：显示「暂无法确定」降级文案，不伪造结论', async () => {
    mockedResolve.mockResolvedValue(makeResolve({ found: false, term: null }))
    const { wrapper } = await mountDrawer()

    await vi.waitFor(() =>
      expect(document.querySelector('[data-test="kinship-unresolved"]')?.textContent).toContain(
        '暂无法确定',
      ),
    )
    expect(document.querySelector('[data-test="kinship-correct-btn"]')).toBeNull()
    expect(document.querySelector('[data-test="kinship-call-btn"]')).toBeNull()
    wrapper.unmount()
  })
})
