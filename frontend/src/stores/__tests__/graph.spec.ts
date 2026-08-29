import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as graphApi from '@/api/graph'
import { useGraphStore } from '@/stores/graph'
import type { GraphData } from '@/types/api'

vi.mock('@/api/graph', () => ({
  fetchMyGraph: vi.fn(),
  createConnectionRequest: vi.fn(),
  fetchIncomingConnections: vi.fn().mockResolvedValue([]),
  resolveConnection: vi.fn(),
  revokeRelation: vi.fn(),
}))

const mockedFetch = vi.mocked(graphApi.fetchMyGraph)

function graph(scope: 'family' | 'clan', id: number): GraphData {
  return {
    nodes: [{ id, name: `成员${id}`, gender: 'unknown', visibility: 'self_private' }],
    edges: [],
    scope,
  }
}

describe('graph store space boundary', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads graph with explicit space_id and records cache scope', async () => {
    mockedFetch.mockResolvedValue(graph('family', 1))
    const store = useGraphStore()

    await store.loadGraph('family', 5, 7)

    expect(mockedFetch).toHaveBeenCalledWith('family', 5, 7)
    expect(store.spaceId).toBe(7)
    expect(store.nodes[0]?.id).toBe(1)
  })

  it('does not call global graph when no active space exists', async () => {
    const store = useGraphStore()
    store.nodes = graph('family', 99).nodes
    store.edges = []

    await store.loadGraph('family', 5, null)

    expect(mockedFetch).not.toHaveBeenCalled()
    expect(store.nodes).toEqual([])
    expect(store.spaceId).toBeNull()
  })

  it('clears previous graph while switching and ignores stale response', async () => {
    let resolveOld: (value: GraphData) => void = () => {}
    mockedFetch
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(graph('family', 2))
    const store = useGraphStore()

    const oldLoad = store.loadGraph('family', 5, 1)
    expect(store.nodes).toEqual([])
    const newLoad = store.loadGraph('family', 5, 2)
    await newLoad
    resolveOld(graph('family', 1))
    await oldLoad

    expect(store.spaceId).toBe(2)
    expect(store.nodes.map((node) => node.id)).toEqual([2])
    expect(mockedFetch).toHaveBeenNthCalledWith(1, 'family', 5, 1)
    expect(mockedFetch).toHaveBeenNthCalledWith(2, 'family', 5, 2)
  })
})
