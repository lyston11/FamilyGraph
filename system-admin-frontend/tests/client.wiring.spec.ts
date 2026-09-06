/**
 * admin API client 接线测试（错误外壳 + 票据 header + 会话过期处理）：
 * - 敏感详情请求带 X-Admin-Access-Session + Bearer access token；
 * - 缺票据 fail-closed，绝不发出无票据敏感请求；
 * - 401 ADMIN_UNAUTHORIZED → refresh 一次并重试；刷新失败 → onSessionExpired；
 * - 登录 401 ADMIN_INVALID_CREDENTIALS 不触发 refresh 流程；
 * - 403 ADMIN_ACCESS_SESSION_INVALID → AccessSessionInvalidError。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosRequestConfig } from 'axios'
import { AxiosError } from 'axios'

import {
  AccessSessionInvalidError,
  AccessSessionRequiredError,
  AdminApiError,
  SESSION_EXPIRED_CODE,
  adminApiClient,
  adminRequest,
  wireAdminApiClient,
} from '@/api/client'

type AdapterMode =
  | { kind: 'ok'; body: unknown }
  | { kind: 'http'; status: number; body: unknown }
  | { kind: 'network' }

let adapterMode: AdapterMode
let capturedHeaders: Record<string, string> = {}
let capturedConfig: AxiosRequestConfig | null = null
let refreshAttempts = 0
let sessionExpiredCalls = 0

function installAdapter(): void {
  adminApiClient.defaults.adapter = async (config) => {
    capturedHeaders = Object.fromEntries(
      Object.entries(config.headers ?? {}).map(([key, value]) => [key.toLowerCase(), String(value)]),
    )
    capturedConfig = config
    if (adapterMode.kind === 'network') {
      throw new Error('network down')
    }
    if (adapterMode.kind === 'http') {
      throw new AxiosError('http error', 'ERR_BAD_REQUEST', config, {}, {
        data: adapterMode.body,
        status: adapterMode.status,
        statusText: '',
        headers: {},
        config,
      } as never)
    }
    return {
      data: adapterMode.body,
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } as never
  }
}

beforeEach(() => {
  installAdapter()
  adapterMode = { kind: 'ok', body: {} }
  capturedHeaders = {}
  capturedConfig = null
  refreshAttempts = 0
  sessionExpiredCalls = 0
  wireAdminApiClient({
    getAccessToken: () => 'access-current',
    getAccessSessionToken: (targetType, targetId) =>
      targetType === 'user' && targetId === 1 ? 'opaque-ticket' : null,
    tryRefresh: () => {
      refreshAttempts += 1
      return Promise.resolve(refreshAttempts === 1)
    },
    onSessionExpired: () => {
      sessionExpiredCalls += 1
    },
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('adminApiClient 接线', () => {
  it('敏感详情请求带 Bearer 与 X-Admin-Access-Session', async () => {
    adapterMode = { kind: 'ok', body: { ok: true } }
    const result = await adminRequest<unknown>({
      method: 'get',
      url: '/v1/users/1/profile',
      accessTarget: { targetType: 'user', targetId: 1 },
    })
    expect(result).toEqual({ ok: true })
    expect(capturedHeaders['authorization']).toBe('Bearer access-current')
    expect(capturedHeaders['x-admin-access-session']).toBe('opaque-ticket')
  })

  it('缺票据的敏感请求 fail-closed，不发出请求', async () => {
    await expect(
      adminRequest<unknown>({
        method: 'get',
        url: '/v1/spaces/5/relations',
        accessTarget: { targetType: 'space', targetId: 5 },
      }),
    ).rejects.toBeInstanceOf(AccessSessionRequiredError)
    expect(capturedConfig).toBeNull()
  })

  it('401 ADMIN_UNAUTHORIZED：refresh 成功后重试一次并换新 token', async () => {
    // 第一次调用返回 401；重试由同一 adapter 序列体现（第一次 401、后续 200）
    let callCount = 0
    adminApiClient.defaults.adapter = async (config) => {
      capturedHeaders = Object.fromEntries(
        Object.entries(config.headers ?? {}).map(([key, value]) => [key.toLowerCase(), String(value)]),
      )
      callCount += 1
      if (callCount === 1) {
        throw new AxiosError('http error', 'ERR_BAD_REQUEST', config, {}, {
          data: { error: { code: 'ADMIN_UNAUTHORIZED', message: '管理员认证失败，请重新登录' } },
          status: 401,
          statusText: '',
          headers: {},
          config,
        } as never)
      }
      return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config } as never
    }

    const result = await adminRequest<unknown>({ method: 'get', url: '/v1/overview' })
    expect(result).toEqual({ ok: true })
    expect(callCount).toBe(2)
    expect(refreshAttempts).toBe(1)
    expect(capturedHeaders['authorization']).toBe('Bearer access-current')
    expect(sessionExpiredCalls).toBe(0)
  })

  it('refresh 也失败：回调 onSessionExpired，错误码 ADMIN_SESSION_EXPIRED', async () => {
    refreshAttempts = 99 // tryRefresh 返回 false
    wireAdminApiClient({
      getAccessToken: () => 'access-current',
      getAccessSessionToken: () => null,
      tryRefresh: () => Promise.resolve(false),
      onSessionExpired: () => {
        sessionExpiredCalls += 1
      },
    })
    adapterMode = {
      kind: 'http',
      status: 401,
      body: { error: { code: 'ADMIN_UNAUTHORIZED', message: '管理员认证失败，请重新登录' } },
    }

    const error = await adminRequest<unknown>({ method: 'get', url: '/v1/overview' }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(AdminApiError)
    expect((error as AdminApiError).code).toBe(SESSION_EXPIRED_CODE)
    expect(sessionExpiredCalls).toBe(1)
  })

  it('登录 401 ADMIN_INVALID_CREDENTIALS 不触发 refresh，保留统一文案', async () => {
    adapterMode = {
      kind: 'http',
      status: 401,
      body: { error: { code: 'ADMIN_INVALID_CREDENTIALS', message: '用户名或密码错误' } },
    }
    const error = await adminRequest<unknown>({ method: 'post', url: '/auth/login' }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(AdminApiError)
    expect((error as AdminApiError).code).toBe('ADMIN_INVALID_CREDENTIALS')
    expect((error as AdminApiError).message).toBe('用户名或密码错误')
    expect(refreshAttempts).toBe(0)
    expect(sessionExpiredCalls).toBe(0)
  })

  it('403 ADMIN_ACCESS_SESSION_INVALID → AccessSessionInvalidError', async () => {
    adapterMode = {
      kind: 'http',
      status: 403,
      body: { error: { code: 'ADMIN_ACCESS_SESSION_INVALID', message: '访问会话无效或已过期' } },
    }
    await expect(
      adminRequest<unknown>({
        method: 'get',
        url: '/v1/users/1/profile',
        accessTarget: { targetType: 'user', targetId: 1 },
      }),
    ).rejects.toBeInstanceOf(AccessSessionInvalidError)
  })

  it('错误外壳 {"error":{code,message}} 正确解析；网络错误归一安全文案', async () => {
    adapterMode = {
      kind: 'http',
      status: 429,
      body: { error: { code: 'ADMIN_INVALID_CREDENTIALS', message: '用户名或密码错误' } },
    }
    const rateLimited = await adminRequest<unknown>({ method: 'post', url: '/auth/login' }).catch(
      (e: unknown) => e,
    )
    expect((rateLimited as AdminApiError).status).toBe(429)
    expect((rateLimited as AdminApiError).code).toBe('ADMIN_INVALID_CREDENTIALS')

    adapterMode = { kind: 'network' }
    const network = await adminRequest<unknown>({ method: 'get', url: '/v1/overview' }).catch(
      (e: unknown) => e,
    )
    expect((network as AdminApiError).code).toBe('NETWORK_ERROR')
    expect((network as AdminApiError).message).toBe('网络连接失败，请稍后重试')
  })

  it('PATCH 请求走统一入口并携带载荷（Provider 更新语义）', async () => {
    adapterMode = { kind: 'ok', body: { id: 3, enabled: false } }
    const result = await adminRequest<unknown>({
      method: 'patch',
      url: '/v1/agent/providers/3',
      data: { enabled: false },
    })
    expect(result).toEqual({ id: 3, enabled: false })
    expect(capturedConfig?.method).toBe('patch')
    expect(capturedConfig?.url).toBe('/v1/agent/providers/3')
    // adapter 层 axios 已把载荷序列化为 JSON 字符串
    expect(JSON.parse(String(capturedConfig?.data))).toEqual({ enabled: false })
  })

  it('AdminApiError 携带错误外壳 detail（如 allowed_models 白名单载荷）', async () => {
    adapterMode = {
      kind: 'http',
      status: 422,
      body: {
        error: {
          code: 'VALIDATION_ERROR',
          message: 'model 不在该 Provider 的 allowed_models 内',
          detail: { allowed_models: ['gpt-5.6-sol'] },
        },
      },
    }
    const error = await adminRequest<unknown>({ method: 'put', url: '/v1/agent/platform-defaults' }).catch(
      (e: unknown) => e,
    )
    expect(error).toBeInstanceOf(AdminApiError)
    expect((error as AdminApiError).code).toBe('VALIDATION_ERROR')
    expect((error as AdminApiError).detail).toEqual({ allowed_models: ['gpt-5.6-sol'] })

    // 无 detail 的错误外壳：detail 保持 undefined
    adapterMode = {
      kind: 'http',
      status: 401,
      body: { error: { code: 'ADMIN_UNAUTHORIZED', message: '管理员认证失败，请重新登录' } },
    }
    const noDetail = await adminRequest<unknown>({ method: 'get', url: '/v1/overview' }).catch(
      (e: unknown) => e,
    )
    expect((noDetail as AdminApiError).detail).toBeUndefined()
  })
})
