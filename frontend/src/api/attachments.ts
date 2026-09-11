import { apiClient } from './client'
import { ApiError } from './errors'

export interface AttachmentOut {
  id: number
  user_id: number
  type: 'image' | 'link' | 'location'
  title: string | null
  description: string | null
  url_or_path: string | null
  created_at: string
}

export async function fetchAttachments(userId: number): Promise<AttachmentOut[]> {
  const { data } = await apiClient.get<AttachmentOut[]>(`/users/${userId}/attachments`)
  return data
}

export async function uploadImage(userId: number, file: File, title = ''): Promise<AttachmentOut> {
  const form = new FormData()
  form.append('file', file)
  if (title) form.append('title', title)
  const { data } = await apiClient.post<AttachmentOut>(
    `/users/${userId}/attachments/image?title=${encodeURIComponent(title)}`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function addLink(
  userId: number,
  payload: { url: string; title?: string; description?: string },
): Promise<AttachmentOut> {
  const { data } = await apiClient.post<AttachmentOut>(`/users/${userId}/attachments/link`, payload)
  return data
}

/**
 * 图片媒体访问（R1，09-11 整改）：
 * - 必须经 `fetchAttachmentBlob` 携带内存 access token 走授权流式端点取图，
 *   禁止把 raw URL 直接塞进 `<img src>`（旧 `attachmentRawUrl` 已删除）；
 *   token 只出现在 Authorization 头，绝不能进 URL query / localStorage / 日志。
 * - 401 由 apiClient 拦截器经现有 refresh single-flight 静默重试一次；
 * - 只接受后端会产出的安全图片 MIME 与 ≤10MB（与上传上限一致），其余 fail-closed；
 * - 403/404 与网络错误统一映射为「照片不可用」安全失败态，不泄露附件存在性。
 */
const ALLOWED_IMAGE_MIME: readonly string[] = ['image/jpeg', 'image/png', 'image/webp']
const MAX_IMAGE_BYTES = 10 * 1024 * 1024

export const MEDIA_UNAVAILABLE_MESSAGE = '照片暂时无法加载'

export async function fetchAttachmentBlob(attachmentId: number, signal?: AbortSignal): Promise<Blob> {
  try {
    const response = await apiClient.get<Blob>(`/attachments/${attachmentId}/raw`, {
      responseType: 'blob',
      signal,
      timeout: 30_000,
    })
    const blob = response.data
    const mime = blob.type.split(';')[0].toLowerCase()
    if (!ALLOWED_IMAGE_MIME.includes(mime)) {
      throw new ApiError(0, 'MEDIA_INVALID', MEDIA_UNAVAILABLE_MESSAGE)
    }
    if (blob.size <= 0 || blob.size > MAX_IMAGE_BYTES) {
      throw new ApiError(0, 'MEDIA_INVALID', MEDIA_UNAVAILABLE_MESSAGE)
    }
    return blob
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw toMediaUnavailableError(error)
  }
}

function toMediaUnavailableError(error: unknown): ApiError {
  // 统一安全文案：401（刷新失败）、403、404 与网络层失败对用户不可区分，
  // 不暴露附件是否存在或失败的具体层级。
  if (error && typeof error === 'object' && 'isAxiosError' in error) {
    const status = (error as { response?: { status?: number } }).response?.status ?? 0
    return new ApiError(status, 'MEDIA_UNAVAILABLE', MEDIA_UNAVAILABLE_MESSAGE)
  }
  return new ApiError(0, 'MEDIA_UNAVAILABLE', MEDIA_UNAVAILABLE_MESSAGE)
}

export async function deleteAttachment(attachmentId: number): Promise<void> {
  await apiClient.delete(`/attachments/${attachmentId}`)
}
