# m1d 技术设计

> 遵守 [architecture.md](../../spec/architecture.md) §5（布局确定性规则）。前置：m1a/m1b/m1c。M1 收口任务。

## 三布局（useLayout composable + Vue Flow 画布）

数据源：`GET /api/graph/me?scope=family&space_id=`（m1b/m1c 已就绪）。

```
composables/useLayout.ts
  computeCanvasLayout(nodes, savedPositions) → 原样坐标 + 新节点环形找位
  computeTreeLayout(nodes, edges)            → d3-hierarchy 树形坐标
      · 层级边 = active elder/younger 边（to_user 为长辈在上）；spouse 同层并列挂靠
      · 多根并列顶层；peer 边不参与层级
      · 异常（环/断链导致无法成树）→ { ok:false } 触发画布回退
  computeListOrder(nodes)                    → 生辰升序，缺省按 id（Q1 默认方案）
node_positions：GET/PUT /api/spaces/{id}/positions（批量存取 {user_id,x,y}）
```

- 布局模式状态存 ui store + localStorage（UI 偏好允许持久化）。
- 节点卡 = MemberCard（头像占位/名字/称谓标签(view.label)/世代角标）；点击开 ProfileDrawer。
- 切换动画用 CSS transition on transform（m4a 再打磨）。

## 后端（极小）

```
GET /api/spaces/{id}/positions   → [{user_id,x,y}]（仅 active 成员可读）
PUT /api/spaces/{id}/positions   → 批量 upsert（active 成员可写）
表 node_positions(id, space_id FK CASCADE, user_id FK CASCADE, x REAL, y REAL,
                  UNIQUE(space_id,user_id))
```
lunar-python 引入：`services/lunar.py` 提供 `solar_to_lunar(date) / lunar_to_solar(...)`
封装（异常→None），POST /users 与 PATCH 在 cal_type=lunar 时自动回填对应公历字段
到 birth/death JSON 的 `solar_date` 键（结构扩展向后兼容）。

## 回退与边界

- 树状布局失败：toast「当前关系较复杂，已切换为自由摆放」+ 自动落 canvas 模式（不丢手动位置）。
- 农历换算失败（超范围日期）：保存原文，另一历置 null，前端提示。

## 验收对照 PRD

三布局切换/位置持久化/世代正确/再婚双 spouse 不崩/农历往返——见 prd.md Acceptance Criteria。
