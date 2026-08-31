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
import { joinByUser } from '@/api/spaces'

/** 图数据与连接请求状态（m1b）。可见性过滤后端强制，store 只做缓存。 */
export const useGraphStore = defineStore('graph', {
  state: () => ({
    nodes: [] as GraphNode[],
    edges: [] as Relation[],
    scope: 'family' as 'family' | 'clan',
    /** Space scope of the cached graph; null means no graph is loaded. */
    spaceId: null as number | null,
    incoming: [] as Relation[],
    loading: false,
    /** Invalidates an earlier request when the space/scope changes or state clears. */
    requestSequence: 0,
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
    async loadGraph(
      scope?: 'family' | 'clan',
      depth = 1,
      spaceId?: number | null,
    ) {
      const target: 'family' | 'clan' = scope ?? this.scope
      const requestedSpaceId = spaceId === undefined ? this.spaceId : spaceId
      // FamilySpaceView always supplies an active space. Without one, do not
      // fall back to the global graph endpoint or retain another space's data.
      if (requestedSpaceId === null) {
        this.requestSequence += 1
        this.nodes = []
        this.edges = []
        this.spaceId = null
        this.scope = target
        this.loading = false
        return
      }
      const sequence = ++this.requestSequence
      // Invalidate the visible cache immediately so a space switch cannot show
      // the previous space while the new graph request is in flight.
      this.nodes = []
      this.edges = []
      this.spaceId = requestedSpaceId
      this.loading = true
      try {
        const data: GraphData = await fetchMyGraph(target, depth, requestedSpaceId)
        if (sequence !== this.requestSequence || this.spaceId !== requestedSpaceId) return
        this.nodes = data.nodes
        this.edges = data.edges
        this.scope = data.scope
      } finally {
        if (sequence === this.requestSequence) this.loading = false
      }
    },
    async loadIncoming() {
      this.incoming = await fetchIncomingConnections()
    },
    async connect(targetId: number, dirClass: DirClass, label: string | null) {
      await createConnectionRequest({ target_id: targetId, dir_class: dirClass, label })
      await this.loadGraph(undefined, 1, this.spaceId)
    },
    async resolve(edgeId: number, action: 'accept' | 'reject') {
      await resolveConnection(edgeId, action)
      await Promise.all([this.loadIncoming(), this.loadGraph(undefined, 1, this.spaceId)])
    },
    async revoke(edgeId: number) {
      await revokeRelation(edgeId)
      await this.loadGraph(undefined, 1, this.spaceId)
    },
    /** 家族视图摘要卡：申请进入对方家庭空间（m2c 端点） */
    async requestJoin(targetUserId: number) {
      await joinByUser(targetUserId)
    },
    clear() {
      this.requestSequence += 1
      this.nodes = []
      this.edges = []
      this.spaceId = null
      this.incoming = []
      this.loading = false
    },
  },
})
