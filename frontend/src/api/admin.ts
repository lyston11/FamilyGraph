import { apiClient } from './client'

export interface AdminUserRow {
  id: number
  name: string
  is_admin: boolean
  gender: string
  privacy_mode: string
  claim_status: string
  created_by: number | null
  locked_until: string | null
  created_at: string
}

export interface AuditRow {
  id: number
  actor_id: number | null
  action: string
  target_id: number | null
  ip: string | null
  detail_json: string | null
  created_at: string | null
}

export async function fetchAdminUsers(): Promise<AdminUserRow[]> {
  const { data } = await apiClient.get<AdminUserRow[]>('/admin/users')
  return data
}

export async function fetchAuditLogs(limit = 200): Promise<AuditRow[]> {
  const { data } = await apiClient.get<AuditRow[]>('/admin/audit-logs', { params: { limit } })
  return data
}

export async function adminResetPin(userId: number): Promise<{ pin: string }> {
  const { data } = await apiClient.post<{ pin: string }>(
    `/admin/users/${userId}/reset-pin`,
    { confirm: true },
  )
  return data
}
