import { describe, expect, it, vi } from 'vitest'

import { apiClient } from '@/api/client'
import { fetchMyGraph } from '@/api/graph'

vi.mock('@/api/client', () => ({
  apiClient: { get: vi.fn() },
}))

describe('graph API', () => {
  it('sends the active space_id when requested', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { nodes: [], edges: [], scope: 'family' },
    })

    await fetchMyGraph('family', 5, 12)

    expect(apiClient.get).toHaveBeenCalledWith('/graph/me', {
      params: { scope: 'family', depth: 5, space_id: 12 },
    })
  })

  it('does not invent a global-space request when no space is provided', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { nodes: [], edges: [], scope: 'clan' },
    })

    await fetchMyGraph('clan', 2)

    expect(apiClient.get).toHaveBeenCalledWith('/graph/me', {
      params: { scope: 'clan', depth: 2 },
    })
  })
})
