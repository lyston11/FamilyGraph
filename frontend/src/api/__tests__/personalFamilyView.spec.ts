import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { fetchPersonalFamilyView } from '@/api/personalFamilyView'
import type { PersonalFamilyViewSnapshot } from '@/types/api'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

function makePayload() {
  return {
    space_id: 7,
    status: 'current',
    view_version: 3,
    computed_at: null,
    nodes: [],
    edges: [],
    truncated: false,
    next_cursor: null,
    stale_reason: null,
  }
}

describe('personal family view API', () => {
  it('requests only the selected space and decodes a valid snapshot', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: makePayload(), headers: {} })

    const snapshot = await fetchPersonalFamilyView(7)
    expect(snapshot).toMatchObject({ data: { space_id: 7 }, etag: null })
    expect(apiClient.get).toHaveBeenCalledWith(
      '/personal-family-view',
      expect.objectContaining({ params: { space_id: 7 } }),
    )
  })

  it('rejects malformed snapshots instead of passing them to the store', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { space_id: 7, nodes: [] } })

    await expect(fetchPersonalFamilyView(7)).rejects.toThrow('个人家族视图响应格式无效')
  })

  it('sends If-None-Match and returns null on 304 so the store keeps its snapshot', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 304, data: undefined, headers: {} })

    await expect(fetchPersonalFamilyView(7, 'W/"v3"')).resolves.toBeNull()
    expect(apiClient.get).toHaveBeenCalledWith(
      '/personal-family-view',
      expect.objectContaining({
        params: { space_id: 7 },
        headers: { 'If-None-Match': 'W/"v3"' },
      }),
    )
  })

  it('captures the response ETag into the snapshot for conditional revalidation', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: makePayload(),
      headers: { etag: 'W/"v4"' },
    })

    const snapshot: PersonalFamilyViewSnapshot | null = await fetchPersonalFamilyView(7)
    expect(snapshot?.etag).toBe('W/"v4"')
  })
})
