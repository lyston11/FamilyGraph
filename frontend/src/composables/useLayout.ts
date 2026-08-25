import { hierarchy, tree } from 'd3-hierarchy'

import type { Relation } from '@/types/api'

export interface LayoutNodeInput {
  id: number
  name: string
  gender: string
}

export interface PositionedNode {
  id: number
  x: number
  y: number
}

/** 画布自由布局：保留已存位置，新成员按环形找空位（m1d U2） */
export function computeCanvasLayout(
  nodes: LayoutNodeInput[],
  savedPositions: Map<number, { x: number; y: number }>,
): PositionedNode[] {
  const out: PositionedNode[] = []
  let ring = 0
  for (const node of nodes) {
    const saved = savedPositions.get(node.id)
    if (saved) {
      out.push({ id: node.id, x: saved.x, y: saved.y })
      continue
    }
    // 新节点：以原点为圆心的螺旋找位
    const angle = (out.length % 8) * (Math.PI / 4) + ring * 0.4
    const radius = 220 + ring * 90
    out.push({ id: node.id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius })
    if (out.length % 8 === 0) ring += 1
  }
  return out
}

interface TreeNodeDatum {
  id: number
  name: string
  children?: TreeNodeDatum[]
}

/**
 * 树状布局（architecture §5 确定性规则）：
 * - 层级边仅取 active 的 elder/younger（to_user 为 from_user 的长辈 → to 在上层）
 * - spouse 不参与层级；peer 边忽略
 * - 多根并列顶层；布局失败返回 { ok:false } 由调用方回退画布模式
 */
export function computeTreeLayout(
  nodes: LayoutNodeInput[],
  edges: Pick<Relation, 'from_user' | 'to_user' | 'dir_class' | 'status'>[],
): { ok: true; positions: PositionedNode[] } | { ok: false; reason: string } {
  const hierarchyEdges: { source: number; target: number }[] = []
  for (const e of edges) {
    if (e.status !== 'active') continue
    // 方向语义：dir_class 描述 to_user 相对 from_user 的身份
    // elder 边：to_user 是长辈 → to 为父节点；younger 边：to_user 是晚辈 → from 为父节点
    if (e.dir_class === 'elder') hierarchyEdges.push({ source: e.from_user, target: e.to_user })
    else if (e.dir_class === 'younger') hierarchyEdges.push({ source: e.to_user, target: e.from_user })
    // peer/spouse：不进层级
  }

  // 长辈方向 parent(target) ← child(source)；构造 children 树
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const childMap = new Map<number, number[]>()
  const hasParent = new Set<number>()
  for (const he of hierarchyEdges) {
    if (!byId.has(he.source) || !byId.has(he.target)) continue
    const list = childMap.get(he.target) ?? []
    list.push(he.source)
    childMap.set(he.target, list)
    hasParent.add(he.source)
  }

  const roots = nodes.filter((n) => !hasParent.has(n.id)).map((n) => n.id)

  function build(id: number, seen: Set<number>): TreeNodeDatum {
    seen.add(id)
    const children = (childMap.get(id) ?? []).filter((c) => !seen.has(c))
    return {
      id,
      name: byId.get(id)?.name ?? String(id),
      children: children.map((c) => build(c, seen)),
    }
  }

  try {
    const layout = tree<TreeNodeDatum>().nodeSize([180, 140])
    const positions: PositionedNode[] = []
    const rootIds = roots.length > 0 ? roots : nodes.length > 0 ? [nodes[0].id] : []

    // 多根并列：每棵子树横向排开
    let offsetX = 0
    for (const rootId of rootIds) {
      if (!byId.has(rootId)) continue
      const seen = new Set<number>()
      const datum = build(rootId, seen)
      const h = hierarchy(datum)
      layout(h)
      for (const d of h.descendants()) {
        positions.push({ id: d.data.id, x: d.x! + offsetX, y: d.depth * 160 })
        seen.add(d.data.id)
      }
      // 统计该树宽度做偏移
      const xs = positions.slice(-h.descendants().length).map((p) => p.x)
      const width = Math.max(...xs) - Math.min(...xs)
      offsetX += width + 260
    }
    // 未入树的孤立节点（如仅配偶关系）排在底部一行
    const placed = new Set(positions.map((p) => p.id))
    let lonelyX = 0
    for (const n of nodes) {
      if (!placed.has(n.id)) {
        positions.push({ id: n.id, x: lonelyX, y: (rootIds.length > 0 ? 6 : 2) * 160 })
        lonelyX += 200
        placed.add(n.id)
      }
    }
    if (positions.length !== nodes.length) return { ok: false, reason: 'coverage' }
    return { ok: true, positions }
  } catch (error) {
    return {
      ok: false,
      reason: error instanceof Error ? error.message : 'layout failed',
    }
  }
}

/** 列表布局：生辰升序（ISO 字符串序），缺生日按 id 升序兜底（Q1 默认方案） */
export function computeListOrder<T extends { id: number; birth: { date: string | null } | null }>(
  members: T[],
): T[] {
  return [...members].sort((a, b) => {
    const da = a.birth?.date ?? null
    const db = b.birth?.date ?? null
    if (da && db) return da < db ? -1 : da > db ? 1 : a.id - b.id
    if (da) return -1
    if (db) return 1
    return a.id - b.id
  })
}
