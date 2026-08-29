import { describe, expect, it } from 'vitest'

import {
  computeCanvasLayout,
  computeGenerationLanes,
  computeListOrder,
  computeTreeLayout,
} from '../useLayout'

const N = (id: number, name: string) => ({ id, name, gender: 'unknown' })

describe('computeCanvasLayout', () => {
  it('保留已存位置，新成员自动落位', () => {
    const nodes = [N(1, '我'), N(2, '父'), N(3, '子')]
    const saved = new Map([[2, { x: -100, y: 50 }]])
    const out = computeCanvasLayout(nodes, saved)
    expect(out.find((p) => p.id === 2)).toEqual({ id: 2, x: -100, y: 50 })
    expect(out).toHaveLength(3)
    // 新节点不与已存位置完全重合
    const p1 = out.find((p) => p.id === 1)!
    expect([p1.x, p1.y]).not.toEqual([-100, 50])
  })
})

describe('computeTreeLayout', () => {
  it('长辈在上、晚辈在下（elder 边 target 为长辈）', () => {
    const nodes = [N(10, '爷爷'), N(20, '爸爸'), N(30, '我')]
    const edges = [
      { from_user: 20, to_user: 10, dir_class: 'elder', status: 'active' },
      { from_user: 30, to_user: 20, dir_class: 'elder', status: 'active' },
    ] as const
    const result = computeTreeLayout(nodes, JSON.parse(JSON.stringify(edges)))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    const y = new Map(result.positions.map((p) => [p.id, p.y]))
    expect(y.get(10)!).toBeLessThan(y.get(20)!)
    expect(y.get(20)!).toBeLessThan(y.get(30)!)
  })

  it('younger 边：to_user（晚辈）挂在下方', () => {
    const nodes = [N(1, '母'), N(2, '儿')]
    const edges = [{ from_user: 1, to_user: 2, dir_class: 'younger', status: 'active' }]
    const result = computeTreeLayout(nodes, JSON.parse(JSON.stringify(edges)))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    const y = new Map(result.positions.map((p) => [p.id, p.y]))
    expect(y.get(1)!).toBeLessThan(y.get(2)!)
  })

  it('多根并列顶层', () => {
    const nodes = [N(1, '甲家祖'), N(2, '乙家祖')]
    const edges: never[] = []
    const result = computeTreeLayout(nodes, edges)
    expect(result.ok).toBe(true)
    if (!result.ok) return
    const y = new Map(result.positions.map((p) => [p.id, p.y]))
    expect(y.get(1)).toBe(y.get(2))
  })

  it('仅配偶/peer 边的孤立节点排底部一行且覆盖全部节点', () => {
    const nodes = [N(1, '夫'), N(2, '妻'), N(3, '友')]
    const edges = [
      { from_user: 1, to_user: 2, dir_class: 'spouse', status: 'active' },
      { from_user: 3, to_user: 1, dir_class: 'peer', status: 'active' },
    ]
    const result = computeTreeLayout(nodes, JSON.parse(JSON.stringify(edges)))
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(new Set(result.positions.map((p) => p.id))).toEqual(new Set([1, 2, 3]))
  })
})

describe('computeGenerationLanes（世代泳道底纹带，P3-3）', () => {
  it('按树状布局行距分层：root 层为第 1 代，band 垂直居中于世代行', () => {
    const positions = [
      { id: 1, x: 0, y: 0 },
      { id: 2, x: 180, y: 160 },
      { id: 3, x: 90, y: 320 },
    ]
    const lanes = computeGenerationLanes(positions)
    expect(lanes.map((l) => l.generation)).toEqual([1, 2, 3])
    expect(lanes[0].y).toBe(-80)
    expect(lanes[0].height).toBe(160)
    expect(lanes[2].y).toBe(240)
  })

  it('底纹带横向覆盖最宽层并留边距（默认 padX=120）', () => {
    const positions = [
      { id: 1, x: 40, y: 0 },
      { id: 2, x: 220, y: 160 },
    ]
    const lanes = computeGenerationLanes(positions)
    expect(lanes[0].x).toBe(-80)
    expect(lanes[0].width).toBe(420)
  })

  it('同代多根与隔代孤立行各成一带；空布局返回空数组', () => {
    expect(computeGenerationLanes([])).toEqual([])
    const positions = [
      { id: 1, x: 0, y: 0 },
      { id: 2, x: 200, y: 0 },
      { id: 3, x: 100, y: 320 },
    ]
    const lanes = computeGenerationLanes(positions)
    expect(lanes.map((l) => l.generation)).toEqual([1, 3])
    expect(lanes[1].y).toBe(240)
  })
})

describe('computeListOrder', () => {
  it('生辰升序，缺生日按 id 兜底（Q1 默认）', () => {
    const members = [
      { id: 5, birth: null },
      { id: 3, birth: { date: '1990-01-01' } },
      { id: 9, birth: null },
      { id: 1, birth: { date: '1950-06-01' } },
    ]
    const ordered = computeListOrder(members)
    expect(ordered.map((m) => m.id)).toEqual([1, 3, 5, 9])
  })
})
