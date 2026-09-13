import type { PersonalFamilyViewTopologyEdge } from '@/types/api'

/**
 * 家族树确定性世代布局（09-13 design.md §6，纯函数）：
 *
 * - 输入已解码节点 id 集合与 confirmed 结构边，输出每人画布坐标；
 * - 世代约束：parent 边要求 rank(子女) = rank(家长) + 1，对称边（配偶/伴侣/
 *   兄弟姐妹）要求两端同代；共享父母的兄弟姐妹自然同代——不通过称谓字符串、
 *   性别或生日推断，不补造父母或婚姻；
 * - 按「稳定 key 排序 → 连通分量 → 分量内约束传播 → 本人分量平移 →
 *   夫妻/伴侣横向布局块 → 有限次重心整理 → 消除同代重叠」执行；
 * - 世代约束矛盾时返回 null，由视图回退自由画布并提示，绝不静默删除
 *   关系、复制人物或无限迭代；
 * - 布局块只是几何分组，不是新事实或新人物，不计入人数。
 */

export interface FamilyTreeLayoutInput {
  /** 全部人物节点 id（每人唯一；由画布模型保证来自已解码快照） */
  userIds: number[]
  edges: readonly PersonalFamilyViewTopologyEdge[]
  viewerId: number | null
  /** 与 MemberNode 现有几何一致的量级（卡片 192px / 列距 280 / 行距 240） */
  cardWidth?: number
  colSpacing?: number
  rowSpacing?: number
}

/** 布局失败（世代约束矛盾）：视图回退自由画布并提示 */
export type FamilyTreeLayoutResult = Map<number, { x: number; y: number }> | null

export const LAYOUT_CARD_WIDTH = 192
export const LAYOUT_COL_SPACING = 280
export const LAYOUT_ROW_SPACING = 240

/** 分量之间的纵向隔离（行距倍数），断开分量保留间隔 */
const COMPONENT_GAP_ROWS = 1.5
/** 布局块之间的最小横向间隔（px） */
const BLOCK_GAP = 24
/** 重心整理固定迭代次数（有界，不无限循环） */
const BARYCENTER_PASSES = 4

interface AdjacencyLink {
  other: number
  /** parent：约束 rank(child)=rank(parent)+1；sym：约束两端同代 */
  kind: 'parent' | 'sym'
  /** parent 边时：self 是否为 other 的家长 */
  selfIsParent: boolean
}

function buildAdjacency(
  userIds: readonly number[],
  edges: readonly PersonalFamilyViewTopologyEdge[],
): Map<number, AdjacencyLink[]> {
  const adjacency = new Map<number, AdjacencyLink[]>()
  for (const id of userIds) adjacency.set(id, [])
  for (const edge of edges) {
    if (!adjacency.has(edge.from_user_id) || !adjacency.has(edge.to_user_id)) continue
    if (edge.edge_kind === 'parent') {
      adjacency.get(edge.from_user_id)!.push({ other: edge.to_user_id, kind: 'parent', selfIsParent: true })
      adjacency.get(edge.to_user_id)!.push({ other: edge.from_user_id, kind: 'parent', selfIsParent: false })
    } else {
      // spouse/partner/sibling：无向对称约束
      adjacency.get(edge.from_user_id)!.push({ other: edge.to_user_id, kind: 'sym', selfIsParent: false })
      adjacency.get(edge.to_user_id)!.push({ other: edge.from_user_id, kind: 'sym', selfIsParent: false })
    }
  }
  return adjacency
}

/** 连通分量：按最小成员 id 稳定排序，孤立节点自成分量 */
function connectedComponents(userIds: readonly number[], adjacency: Map<number, AdjacencyLink[]>): number[][] {
  const visited = new Set<number>()
  const components: number[][] = []
  for (const seed of userIds) {
    if (visited.has(seed)) continue
    const members: number[] = []
    const queue = [seed]
    visited.add(seed)
    while (queue.length > 0) {
      const current = queue.shift()!
      members.push(current)
      for (const link of adjacency.get(current) ?? []) {
        if (!visited.has(link.other)) {
          visited.add(link.other)
          queue.push(link.other)
        }
      }
    }
    components.push(members.sort((a, b) => a - b))
  }
  return components
}

/**
 * 分量内世代约束传播：从最小 id 出发 BFS，parent 边 child=parent+1、
 * 对称边相等；已赋值节点与约束不符即世代矛盾。每个节点至多入队一次，有界。
 */
function assignComponentRanks(
  members: readonly number[],
  adjacency: Map<number, AdjacencyLink[]>,
): Map<number, number> | null {
  const ranks = new Map<number, number>()
  const seed = members[0]
  ranks.set(seed, 0)
  const queue = [seed]
  while (queue.length > 0) {
    const current = queue.shift()!
    const currentRank = ranks.get(current)!
    for (const link of adjacency.get(current) ?? []) {
      const expected =
        link.kind === 'sym'
          ? currentRank
          : link.selfIsParent
            ? currentRank + 1
            : currentRank - 1
      const known = ranks.get(link.other)
      if (known === undefined) {
        ranks.set(link.other, expected)
        queue.push(link.other)
      } else if (known !== expected) {
        return null
      }
    }
  }
  return ranks
}

interface LayoutBlock {
  members: number[]
  /** 成员在块内的横向序号（成员按 id 升序） */
  indexOf: (userId: number) => number
  x: number
}

/** 同代内按对称边（配偶/伴侣）做并查集分块；块内按稳定 id 排序 */
function buildBlocks(rank: number[], rankByUser: ReadonlyMap<number, number>, adjacency: Map<number, AdjacencyLink[]>): LayoutBlock[] {
  const parent = new Map<number, number>()
  const find = (id: number): number => {
    let root = id
    while (parent.get(root) !== root) root = parent.get(root)!
    while (parent.get(id) !== root) {
      const next = parent.get(id)!
      parent.set(id, root)
      id = next
    }
    return root
  }
  const union = (a: number, b: number): void => {
    parent.set(find(a), find(b))
  }
  for (const id of rank) parent.set(id, id)
  for (const id of rank) {
    for (const link of adjacency.get(id) ?? []) {
      if (link.kind === 'sym' && rankByUser.get(link.other) === rankByUser.get(id)) {
        union(id, link.other)
      }
    }
  }

  const grouped = new Map<number, number[]>()
  for (const id of rank) {
    const root = find(id)
    const list = grouped.get(root) ?? []
    list.push(id)
    grouped.set(root, list)
  }
  const blocks: LayoutBlock[] = []
  for (const members of grouped.values()) {
    members.sort((a, b) => a - b)
    const index = new Map(members.map((id, i) => [id, i]))
    blocks.push({ members, indexOf: (userId) => index.get(userId) ?? 0, x: 0 })
  }
  return blocks
}

/** 布局块宽度：成员按列距排开，两端各留半张卡 */
function blockWidth(block: LayoutBlock, cardWidth: number, colSpacing: number): number {
  if (block.members.length === 0) return cardWidth
  return (block.members.length - 1) * colSpacing + cardWidth
}

/** 把一行的块按当前顺序左到右排开，保证块间最小间隔（消除重叠） */
function placeRow(blocks: LayoutBlock[], cardWidth: number, colSpacing: number): void {
  let cursor = 0
  for (const block of blocks) {
    block.x = cursor
    cursor += blockWidth(block, cardWidth, colSpacing) + BLOCK_GAP
  }
}

export function computeFamilyTreeLayout(input: FamilyTreeLayoutInput): FamilyTreeLayoutResult {
  const cardWidth = input.cardWidth ?? LAYOUT_CARD_WIDTH
  const colSpacing = input.colSpacing ?? LAYOUT_COL_SPACING
  const rowSpacing = input.rowSpacing ?? LAYOUT_ROW_SPACING

  const userIds = [...new Set(input.userIds)].sort((a, b) => a - b)
  if (userIds.length === 0) return new Map()

  const adjacency = buildAdjacency(userIds, input.edges)
  const components = connectedComponents(userIds, adjacency)

  // 世代约束传播（任一分量矛盾即整体回退）
  const rankByUser = new Map<number, number>()
  for (const members of components) {
    const ranks = assignComponentRanks(members, adjacency)
    if (ranks === null) return null
    for (const [id, rank] of ranks) rankByUser.set(id, rank)
  }

  // 分量摆位：本人分量最先（平移令本人 rank=0），其余分量按种子 id 依次排在下方
  const viewerComponent =
    input.viewerId !== null ? components.find((members) => members.includes(input.viewerId!)) : undefined
  const orderedComponents = viewerComponent
    ? [viewerComponent, ...components.filter((members) => members !== viewerComponent)]
    : components

  const shiftedRank = new Map<number, number>()
  const componentBands: { members: number[]; maxRank: number }[] = []
  let bandCursor = 0
  for (const members of orderedComponents) {
    const internalRanks = members.map((id) => rankByUser.get(id) ?? 0)
    let shift: number
    if (members === viewerComponent && input.viewerId !== null) {
      shift = -internalRanks[members.indexOf(input.viewerId)]
    } else {
      shift = bandCursor - Math.min(...internalRanks)
    }
    let maxRank = Number.NEGATIVE_INFINITY
    for (const id of members) {
      const rank = (rankByUser.get(id) ?? 0) + shift
      shiftedRank.set(id, rank)
      maxRank = Math.max(maxRank, rank)
    }
    componentBands.push({ members, maxRank })
    bandCursor = maxRank + COMPONENT_GAP_ROWS
  }

  // 每个分量独立成行带：按 rank 分行、行内按对称关系分块
  const positions = new Map<number, { x: number; y: number }>()
  for (const band of componentBands) {
    const rows = new Map<number, number[]>()
    for (const id of band.members) {
      const rank = shiftedRank.get(id)!
      const list = rows.get(rank) ?? []
      list.push(id)
      rows.set(rank, list)
    }
    const sortedRanks = [...rows.keys()].sort((a, b) => a - b)
    const rowBlocks = new Map<number, LayoutBlock[]>()
    for (const rank of sortedRanks) {
      const blocks = buildBlocks(rows.get(rank)!, shiftedRank, adjacency)
      blocks.sort((a, b) => a.members[0] - b.members[0])
      rowBlocks.set(rank, blocks)
      placeRow(blocks, cardWidth, colSpacing)
    }

    // 有限次重心整理：奇数轮自上而下按上一代重心排序，偶数轮自下而上按下一代
    for (let pass = 0; pass < BARYCENTER_PASSES; pass += 1) {
      const ascending = pass % 2 === 0
      const ranks = ascending ? sortedRanks : [...sortedRanks].reverse()
      for (const rank of ranks) {
        const blocks = rowBlocks.get(rank)!
        const neighborRank = ascending ? rank - 1 : rank + 1
        const scored = blocks.map((block, index) => {
          const neighbors: number[] = []
          for (const id of block.members) {
            for (const link of adjacency.get(id) ?? []) {
              if (shiftedRank.get(link.other) === neighborRank) neighbors.push(link.other)
            }
          }
          if (neighbors.length === 0) {
            return { block, key: block.x, index }
          }
          const barycenter =
            neighbors.reduce((sum, id) => {
              const owner = rowBlocks.get(neighborRank)?.find((candidate) => candidate.members.includes(id))
              if (!owner) return sum
              return sum + owner.x + owner.indexOf(id) * colSpacing
            }, 0) / neighbors.length
          return { block, key: barycenter, index }
        })
        scored.sort((a, b) => a.key - b.key || a.index - b.index)
        rowBlocks.set(
          rank,
          scored.map((entry) => entry.block),
        )
        placeRow(rowBlocks.get(rank)!, cardWidth, colSpacing)
      }
    }

    for (const rank of sortedRanks) {
      for (const block of rowBlocks.get(rank)!) {
        for (const id of block.members) {
          positions.set(id, {
            x: block.x + block.indexOf(id) * colSpacing,
            y: rank * rowSpacing,
          })
        }
      }
    }
  }

  return positions
}
