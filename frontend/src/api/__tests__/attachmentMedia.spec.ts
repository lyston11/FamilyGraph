import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { ApiError } from '@/api/errors'
import {
  MEDIA_UNAVAILABLE_MESSAGE,
  fetchAttachmentBlob,
} from '@/api/attachments'

// 媒体认证合同（R1，09-11 整改）：
// - fetchAttachmentBlob 走 apiClient（携带内存 access token 的 Bearer 头），
//   401 由统一拦截器经 refresh single-flight 重试，token 绝不进 URL query；
// - 只接受安全图片 MIME 与 ≤10MB；
// - 403/404/网络失败统一映射为安全失败态文案，不泄露附件存在性。
vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

const mockedGet = vi.mocked(apiClient.get)

function imageBlob(mime = 'image/png', size = 1024): Blob {
  return { type: mime, size } as unknown as Blob
}

describe('fetchAttachmentBlob 媒体认证合同', () => {
  beforeEach(() => {
    mockedGet.mockReset()
  })

  it('请求 raw 端点且 URL 不含任何 token 参数', async () => {
    mockedGet.mockResolvedValue({ data: imageBlob() })

    await fetchAttachmentBlob(42)

    expect(mockedGet).toHaveBeenCalledTimes(1)
    const [url, config] = mockedGet.mock.calls[0]
    expect(url).toBe('/attachments/42/raw')
    expect(String(url)).not.toContain('token')
    expect(String(url)).not.toContain('Bearer')
    // Bearer 由 apiClient 请求拦截器统一注入内存 token，而非 URL
    expect((config as { responseType?: string }).responseType).toBe('blob')
  })

  it('只接受安全图片 MIME；HTML/SVG 等响应 fail-closed', async () => {
    mockedGet.mockResolvedValue({ data: imageBlob('text/html') })
    await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({ code: 'MEDIA_INVALID' })

    mockedGet.mockResolvedValue({ data: imageBlob('image/svg+xml') })
    await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({ code: 'MEDIA_INVALID' })

    mockedGet.mockResolvedValue({ data: imageBlob('image/webp') })
    await expect(fetchAttachmentBlob(1)).resolves.toBeDefined()
  })

  it('拒绝空响应与超过 10MB 的图片', async () => {
    mockedGet.mockResolvedValue({ data: imageBlob('image/png', 0) })
    await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({ code: 'MEDIA_INVALID' })

    mockedGet.mockResolvedValue({ data: imageBlob('image/png', 10 * 1024 * 1024 + 1) })
    await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({ code: 'MEDIA_INVALID' })
  })

  it('403/404/网络失败统一映射为安全失败态，不泄露存在性', async () => {
    for (const status of [403, 404]) {
      mockedGet.mockRejectedValue({ isAxiosError: true, response: { status } })
      await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({
        code: 'MEDIA_UNAVAILABLE',
        message: MEDIA_UNAVAILABLE_MESSAGE,
      })
    }
    mockedGet.mockRejectedValue(new Error('network down'))
    await expect(fetchAttachmentBlob(1)).rejects.toMatchObject({ code: 'MEDIA_UNAVAILABLE' })
  })

  it('ApiError 原样透传（401 刷新失败路径由拦截器抛出）', async () => {
    mockedGet.mockRejectedValue(new ApiError(401, 'AUTH', 'session expired'))
    await expect(fetchAttachmentBlob(1)).rejects.toBeInstanceOf(ApiError)
  })
})
