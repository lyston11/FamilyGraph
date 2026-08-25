import { defineStore } from 'pinia'

import type {
  DirClass,
  GraphData,
  GraphNode,
  Relation,
} from '@/types/api'

import {
  createConnectionRequest,
  fetchIncomingConnections,
  fetchMyGraph,
  resolveConnection,
  revokeRelation,
} from '@/api/graph'

/** 图数据与连接请求状态（m1b）。可见性过滤后端强制，store 只做缓存。 */
export const useGraphStore = defineStore('graph', {
  state: () => ({
    nodes: [] as GraphNode[],
    edges: [] as Relation[],
    scope: 'family' as 'family' | 'clan',
    incoming: [] as Relation[],
    loading: false,
  }),
  getters: {
    pendingIncomingCount(state): number {
      return state.incoming.filter((r) => r.status === 'pending').length
    },
    /** id → 节点映射，供卡片/连线取名字 */
    nodeById(state): Map<number, GraphNode> {
      const map = new Map<number, GraphNode>()
      for (const n of state.nodes) map.set(n.id, n)
      return map
    },
  },
  actions: {
    async loadGraph(scope?: 'family' | 'clan', depth = 1) {
      const target: 'family' | 'clan' = scope ?? this.scope
      this.loading = true
      try {
        const data: GraphData = await fetchMyGraph(target, depth)
        this.nodes = data.nodes
        this.edges = data.edges
        this.scope = data.scope
      } finally {
        this.loading = false
      }
    },
    async loadIncoming() {
      this.incoming = await fetchIncomingConnections()
    },
    async connect(targetId: number, dirClass: DirClass, label: string | null) {
      await createConnectionRequest({ target_id: targetId, dir_class: dirClass, label })
      await this.loadGraph()
    },
    async resolve(edgeId: number, action: 'accept' | 'reject') {
      await resolveConnection(edgeId, action)
      await Promise.all([this.loadIncoming(), this.loadGraph()])
    },
    async revoke(edgeId: number) {
      await revokeRelation(edgeId)
      await this.loadGraph()
    },
    clear() {
      this.nodes = []
      this.edges = []
      this.incoming = []
    },
  },
})
