/**
 * 类型对齐冒烟（type-safety spec：API 类型与后端 Pydantic 字段一一对应）：
 * - 后端精确字段集 fixture 可解码（AdminProfileOut 无 updated_at、附件无 size）；
 * - AdminAgentErrorOut 只保留四个安全字段（多余字段不进视图）；
 * - 错误外壳/分页 envelope/枚举与后端 app/errors.py、AdminPageOut 对齐；
 * - 非法值（错误枚举/缺字段）被解码器拒绝，不让 undefined 流入组件。
 */
import { describe, expect, it, vi } from 'vitest'

import { AdminApiError } from '@/api/client'
import { ContractViolationError, expectLiteral } from '@/api/decode'
import {
  apiCreateAccessSession,
  apiOverview,
  apiSpaceMembers,
  apiUserAttachments,
  apiUserProfile,
  decodeAgentError,
} from '@/api/read'
import { decodeAdminSessionOut } from '@/api/auth'
import { ADMIN_ERROR_CODES } from '@/types/api'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, adminRequest: vi.fn() }
})

import { adminRequest } from '@/api/client'

const mockedAdminRequest = vi.mocked(adminRequest)

describe('类型对齐冒烟（后端合同）', () => {
  it('AdminProfileOut 精确白名单：无 updated_at（User 无此列）', async () => {
    const backendFixture = {
      id: 21,
      name: '李秀英',
      gender: 'female',
      birth: { cal_type: 'lunar', date: '1948-03-12', mirror_date: '1948-04-20' },
      death: null,
      bio: null,
      avatar_available: true,
      profile_status: 'identity_confirmed',
      claim_status: 'claimed',
      created_at: '2026-08-02T00:00:00Z',
    }
    mockedAdminRequest.mockResolvedValueOnce(backendFixture)
    const profile = await apiUserProfile(21)
    expect(profile.name).toBe('李秀英')
    // 白名单键集合必须与后端 schema 一致
    expect(Object.keys(profile).sort()).toEqual(
      [
        'avatar_available',
        'bio',
        'birth',
        'claim_status',
        'created_at',
        'death',
        'gender',
        'id',
        'name',
        'profile_status',
      ].sort(),
    )
    expect(Object.keys(profile)).not.toContain('updated_at')
    expect(Object.keys(profile)).not.toContain('avatar_path')
  })

  it('附件元数据无 size / url_or_path / description（Attachment 无 size 列）', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      items: [
        { id: 3, type: 'image', title_safe: '族谱扫描件', created_at: '2026-08-03T00:00:00Z' },
      ],
      page: 1,
      page_size: 10,
      total: 1,
      has_more: false,
    })
    const page = await apiUserAttachments(21, { page: 1, pageSize: 10 })
    expect(Object.keys(page.items[0]).sort()).toEqual(['created_at', 'id', 'title_safe', 'type'].sort())
    expect(Object.keys(page.items[0])).not.toContain('size')
    expect(Object.keys(page.items[0])).not.toContain('url_or_path')
  })

  it('AdminAgentErrorOut 只保留 error_code/component/stack_location/summary 四字段', async () => {
    // 后端即使误发多余字段，前端解码对象也只有四个安全键
    const decoded = decodeAgentError({
      error_code: 'X',
      component: 'c',
      stack_location: 's',
      summary: 'm',
      prompt: 'SHOULD_NOT_APPEAR',
      message: 'SHOULD_NOT_APPEAR',
    })
    expect(Object.keys(decoded ?? {}).sort()).toEqual(
      ['component', 'error_code', 'stack_location', 'summary'].sort(),
    )
    expect(JSON.stringify(decoded)).not.toContain('SHOULD_NOT_APPEAR')
  })

  it('AdminSessionOut 硬校验：缺字段/状态枚举错被拒绝', () => {
    expect(decodeAdminSessionOut({ id: 1, username: 'a', password_must_change: true, status: 'claimed' })).toEqual({
      id: 1,
      username: 'a',
      password_must_change: true,
      status: 'claimed',
    })
    expect(() => decodeAdminSessionOut({ id: 1, username: 'a', status: 'claimed' })).toThrow(
      ContractViolationError,
    )
    expect(() =>
      decodeAdminSessionOut({ id: 1, username: 'a', password_must_change: true, status: 'admin' }),
    ).toThrow(ContractViolationError)
  })

  it('AdminPageOut envelope 与 overview totals 对齐（缺字段被拒）', async () => {
    const ok = {
      items: [
        {
          space_id: 1,
          name: 'A',
          kind: 'household',
          created_at: '2026-08-01T00:00:00Z',
          manager_user_id: null,
          manager_name: null,
          member_count: 3,
          status: 'healthy',
          anomalies: [],
        },
      ],
      page: 1,
      page_size: 50,
      total: 1,
      has_more: false,
      totals: {
        spaces_total: 1,
        healthy_spaces: 1,
        anomaly_spaces: 0,
        active_space_admins: 1,
        pending_applications: 0,
      },
    }
    mockedAdminRequest.mockResolvedValueOnce(ok)
    const page = await apiOverview({ page: 1 })
    expect(page.totals.spaces_total).toBe(1)
    expect(page.items[0].status).toBe('healthy')

    const broken = { ...ok, totals: { spaces_total: 1 } }
    mockedAdminRequest.mockResolvedValueOnce(broken)
    await expect(apiOverview({ page: 1 })).rejects.toThrow(ContractViolationError)
  })

  it('成员行携带 updated_at（SpaceMember 有该列），与档案投影形成对照', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      items: [
        {
          user_id: 21,
          name: '李秀英',
          role: 'space_admin',
          status: 'active',
          created_at: '2026-08-02T00:00:00Z',
          updated_at: '2026-08-09T00:00:00Z',
        },
      ],
      page: 1,
      page_size: 50,
      total: 1,
      has_more: false,
    })
    const page = await apiSpaceMembers(1, { page: 1 })
    expect(page.items[0].updated_at).toBe('2026-08-09T00:00:00Z')
    expect(page.items[0].role).toBe('space_admin')
  })

  it('访问会话响应解码：session_id 为明文票据且只出现在内存路径', async () => {
    mockedAdminRequest.mockResolvedValueOnce({
      session_id: 'opaque',
      target_type: 'space',
      target_id: 1,
      allowed_scopes: ['relation.detail', 'fact.detail'],
      issued_at: '2026-09-04T00:00:00Z',
      expires_at: '2026-09-04T00:30:00Z',
    })
    const out = await apiCreateAccessSession({ target_type: 'space', target_id: 1, reason: 'r' })
    expect(out.session_id).toBe('opaque')
    expect(out.allowed_scopes).toEqual(['relation.detail', 'fact.detail'])
  })

  it('错误码常量与后端 app/errors.py admin 子集一致', () => {
    expect(ADMIN_ERROR_CODES.INVALID_CREDENTIALS).toBe('ADMIN_INVALID_CREDENTIALS')
    expect(ADMIN_ERROR_CODES.UNAUTHORIZED).toBe('ADMIN_UNAUTHORIZED')
    expect(ADMIN_ERROR_CODES.PASSWORD_CHANGE_REQUIRED).toBe('ADMIN_PASSWORD_CHANGE_REQUIRED')
    expect(ADMIN_ERROR_CODES.ACCESS_SESSION_INVALID).toBe('ADMIN_ACCESS_SESSION_INVALID')
    expect(ADMIN_ERROR_CODES.TARGET_NOT_FOUND).toBe('ADMIN_TARGET_NOT_FOUND')
    expect(ADMIN_ERROR_CODES.APPLICATION_NOTE_REQUIRED).toBe('SPACE_MANAGER_APPLICATION_NOTE_REQUIRED')
    expect(ADMIN_ERROR_CODES.APPLICATION_DECIDED).toBe('SPACE_MANAGER_APPLICATION_DECIDED')
  })

  it('AdminApiError 保留外壳 code/message；非法枚举被 expectLiteral 拒绝', async () => {
    const error = new AdminApiError(403, 'ADMIN_PASSWORD_CHANGE_REQUIRED', '请先修改初始管理员密码后再继续操作')
    expect(error.status).toBe(403)
    expect(error.code).toBe('ADMIN_PASSWORD_CHANGE_REQUIRED')

    expect(() => expectLiteral('big', ['small', 'large'] as const, 'size')).toThrow(
      ContractViolationError,
    )
  })
})
