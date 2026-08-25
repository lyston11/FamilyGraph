import { apiClient } from './client'

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

/** 授权流式下载/内联预览 */
export function attachmentRawUrl(attachmentId: number): string {
  return `/api/attachments/${attachmentId}/raw`
}

export async function deleteAttachment(attachmentId: number): Promise<void> {
  await apiClient.delete(`/attachments/${attachmentId}`)
}
