import type { PersonalFamilyViewTopologyEdge } from '@/types/api'

/**
 * 家族树确定性世代布局（09-13 design.md §6，纯函数）：
 *
 * - 输入已解码节点 id 集合与 confirmed 结构边，输出每人画布坐标；
 * - 世代约束：parent 边要求 rank(子女) = rank(家长) + 1，对称边（配偶/伴侣/
 *   兄弟姐妹）要求两端同代；共享父母的兄弟姐妹自然同代——不通过称谓字符串、
 *   性别或生日推断，不补造父母或婚姻；
 * - 按「稳定 key 排序 → 连通分量 → 分量内约束传播 → 本人分量平移 →
 *   夫妻/共同父母几何块 → 后代区间宽度 → 自顶向下分配独立分支」执行；
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

interface AdjacencyLink {
  other: number
  /** parent：约束 rank(child)=rank(parent)+1；sym：约束两端同代 */
  kind: PersonalFamilyViewTopologyEdge['edge_kind']
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
      adjacency.get(edge.from_user_id)!.push({ other: edge.to_user_id, kind: edge.edge_kind, selfIsParent: false })
      adjacency.get(edge.to_user_id)!.push({ other: edge.from_user_id, kind: edge.edge_kind, selfIsParent: false })
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
        link.kind !== 'parent'
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

/** 配偶块保持相邻；共同父母仅组成几何组，兄弟姐妹只约束同代。 */
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
      if ((link.kind === 'spouse' || link.kind === 'partner') && rankByUser.get(link.other) === rankByUser.get(id)) {
        union(id, link.other)
      }
    }
  }

  // 先记录配偶组，再合并共同父母，防止共同父母的 id 排序拆散配偶。
  const couples = new Map<number, number[]>()
  for (const id of rank) {
    const root = find(id)
    const list = couples.get(root) ?? []
    list.push(id)
    couples.set(root, list)
  }
  const firstParentByChild = new Map<number, number>()
  for (const id of rank) {
    for (const link of adjacency.get(id) ?? []) {
      if (link.kind !== 'parent' || !link.selfIsParent) continue
      const first = firstParentByChild.get(link.other)
      if (first === undefined) firstParentByChild.set(link.other, id)
      else union(first, id)
    }
  }

  const grouped = new Map<number, number[]>()
  for (const members of couples.values()) {
    const root = find(members[0]!)
    grouped.set(root, [...(grouped.get(root) ?? []), ...members])
  }
  const blocks: LayoutBlock[] = []
  for (const members of grouped.values()) {
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

/**
 * 每个几何块只占一个后代区间。跨支系婚姻可能连接多个父代块，此时按
 * 真实亲子连接数、稳定成员 id 选择一个区间所有者；所有事实边照常渲染。
 * rank 已验证严格递增，因此正反两次遍历即可分配宽度，无递归/重复子树。
 */
function placeBranches(
  blocks: LayoutBlock[],
  adjacency: Map<number, AdjacencyLink[]>,
  cardWidth: number,
  colSpacing: number,
): void {
  const ownerByUser = new Map<number, LayoutBlock>()
  const children = new Map(blocks.map((block) => [block, [] as LayoutBlock[]]))
  for (const block of blocks) {
    for (const id of block.members) ownerByUser.set(id, block)
  }
  const roots: LayoutBlock[] = []
  for (const block of blocks) {
    const parents = new Map<LayoutBlock, Set<string>>()
    for (const id of block.members) {
      for (const link of adjacency.get(id) ?? []) {
        if (link.kind !== 'parent' || link.selfIsParent) continue
        const parent = ownerByUser.get(link.other)!
        const links = parents.get(parent) ?? new Set<string>()
        links.add(`${link.other}:${id}`)
        parents.set(parent, links)
      }
    }
    const parent = [...parents].sort((a, b) =>
      b[1].size - a[1].size || a[0].members[0]! - b[0].members[0]!,
    )[0]?.[0]
    if (parent) children.get(parent)!.push(block)
    else roots.push(block)
  }

  const gap = Math.max(BLOCK_GAP, colSpacing - cardWidth)
  const widths = new Map<LayoutBlock, number>()
  const childWidths = new Map<LayoutBlock, number>()
  for (const block of [...blocks].reverse()) {
    const descendants = children.get(block)!
    const width = descendants.reduce((sum, child) => sum + widths.get(child)!, 0)
      + Math.max(0, descendants.length - 1) * gap
    childWidths.set(block, width)
    widths.set(block, Math.max(blockWidth(block, cardWidth, colSpacing), width))
  }
  const leftByBlock = new Map<LayoutBlock, number>()
  let cursor = 0
  for (const root of roots) {
    leftByBlock.set(root, cursor)
    cursor += widths.get(root)! + gap
  }
  for (const block of blocks) {
    const left = leftByBlock.get(block)!
    const width = widths.get(block)!
    block.x = left + (width - blockWidth(block, cardWidth, colSpacing)) / 2
    let childLeft = left + (width - childWidths.get(block)!) / 2
    for (const child of children.get(block)!) {
      leftByBlock.set(child, childLeft)
      childLeft += widths.get(child)! + gap
    }
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
    }
    placeBranches(sortedRanks.flatMap((rank) => rowBlocks.get(rank)!), adjacency, cardWidth, colSpacing)

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
