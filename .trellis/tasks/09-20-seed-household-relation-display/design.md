# 技术设计：种子成员资格修正 + 家庭卡关系称谓

## 1. 事实基座（已在隔离库与运行实例核实）

### 1.1 种子里没有「朱元璋–李贞」关系

真实存在的是两条 confirmed 事实：

| 事实 | 类型 |
|---|---|
| 朱元璋 ⇄ 朱佛女 | `direct_sibling`（姐弟） |
| 李贞 ⇄ 朱佛女 | `spouse`（夫妻） |

即 李贞 是 朱元璋 的**姐夫**。所以问题不是「凭空多了条关系」，而是**同处一室却看不出关系**。

### 1.2 「看不出关系」有两个叠加原因

**(a) 家庭卡不投影关系称谓。** `household_card.py` 的成员行只有
`{user_id, display, household_label, visibility_level}`，`household_label` 只区分「管理员/成员」。同一条关系在个人页（走 PFV `edges`）可见，在家庭卡不可见。

**(b) 朱元璋 视角在「李家」内根本解析不出路径。** 实测：

```
朱元璋 @李家: node_visible=[1,49]  path_visible=[1,2,3,4,5,7,11,12,13,14,15,49]
李贞   @李家: node_visible=[1,49]  path_visible=[1,10,49,50]
```

`李贞→朱元璋` 能算出「妻子的兄弟」，`朱元璋→李贞` **算不出**（`found=False`）。原因：朱元璋 在「李家」household 内没有任何结构事实——他与 朱佛女 是 sibling，而 **sibling 不产生 `Relation`**（只有 elder/younger/spouse 才产生），所以从「李家」出发，李贞 在朱元璋 的图里是孤立节点。

结论：即使只做 (a)，在「李家」这张卡上仍会显示不出称谓。**所以 R1（去掉该成员资格）是让数据自洽的必要前提**，两者确实都该做。

## 2. R1 设计：移除种子中的 朱元璋@李家

### 2.1 名册改动

`dev_seed.py` 的 `_SPACE_ROSTERS["李家"]` 去掉 `("朱元璋", "m")`。

### 2.2 是否补人？——**推荐用 朱佛女 替换**

**推荐：`李家 = 李贞（admin） + 朱佛女（member）`。**

理由：
- 保持 docstring 记载的既有模式「household 2 人（本家 admin + 帝室 member）」，不留下唯一一个 1 人 household；
- 两人是真实配偶，卡片上能显示「妻子/丈夫」，让新增的 R3 功能在演示数据里**可见可验**；
- 「李家」这个名字本就指李贞本家，李贞+朱佛女正是「李氏家族」的构成核心；
- **不扩大任何可见性**：朱佛女 已在「李氏家族」lineage，且经 spouse 关系对 李贞 本就享有 `household_detail`，加入 household 不产生新的授权面。

**备选：只删不补**（李家只剩 李贞，成员数 1）。也可以，但会打破既有模式并使 R3 在李家无从演示。此点请在评审时确认。

### 2.3 数据迁移（0054）

种子收敛是 **insert-only**（`dev_seed.py` 明确：只增不改不删），已存在的行永远删不掉，所以必须一次性数据修正。沿用 0052 的既有模式：

```sql
DELETE FROM space_members WHERE id IN (
  SELECT m.id FROM space_members m
  JOIN family_spaces f ON f.id = m.space_id
  JOIN users u ON u.id = m.user_id
  WHERE m.status = 'active'
    AND m.added_by = f.owner_id          -- 种子插入的行由空间 owner 添加
    AND f.kind = 'household'
    AND f.name = '李家'
    AND u.name = '朱元璋'
)
```

判定条件刻意收窄（空间名 + 用户 + active + added_by=owner），**不会**误删真实用户自己的加入申请或管理员批准的邀请。

同迁移内，若采用推荐方案，则**补建** 朱佛女 在 李家的 active 成员行（幂等：`WHERE NOT EXISTS` 同类检查），以便既有生产库与全新库收敛到同一状态。

**降级**：与 0052 同一约定——数据修正**刻意不逆向**（不恢复已删的成员资格，也不删除补建的行无条件回退）。降级必须在任何 DDL/版本移动**之前**先履行父级 0053 的拒绝合同，并复现 0052 自身的 `_refuse_if_timing_evidence` 等父级 preflight。注意 0053 是**结构迁移**（新增 `space_member_approvals` / `member_relation_labels` 表，前置 `ALTER`），所以 0054 的 downgrade 跨过 0053 时父级合同必须先跑。

### 2.4 只删行不失效投影够不够？——够，但补一次失效更稳

实测：raw delete 成员行后**未重建**的物化 PFV 仍含该成员

```
物化 PFV 节点(删除前): [10, 49]
物化 PFV 节点(raw delete 后、未重建): [10, 49]   # 仍是旧快照
```

但**读取授权不依赖投影**：家庭卡走 `authorized_household_space_or_404`（按 active membership 现算），实测删除后 朱元璋 读「李家」直接 404；且 `current_view_payload` 内部是 `steward_views.payload_for` 阶段化投影，会自愈重建。所以**功能上不需要**在迁移里手改投影表。

但迁移直接删行不会触发 `space.membership.changed` 事件，投影会保留一段时间的陈旧快照（含已移除成员）。为让「迁移后立刻一致」，迁移内对该空间的所有 `personal_family_views` 行**标记 stale**（`status='stale'` + `invalidated_at`/`updated_at`），复用既有 `invalidate_space_views` 的同一语义（**只标 stale，不删行、不写计算内容**）。这一点写进迁移并在测试中断言。

## 3. R3 设计：家庭卡成员带出关系称谓

### 3.1 数据来源：复用已授权的 PFV payload，不新建图加载

`household_card_payload` 已经调用了

```python
payload = personal_family_view.current_view_payload(session, account=account, space_id=space_id)
```

其 `edges` 就是**该 viewer 已授权、viewer 锚定**的关系投影。实测直接可用：

```
李贞: payload edges → {10: '妻子'}，card members = ['朱佛女']
```

因此实现只需：从 `payload["edges"]` 建 `target_user_id → term` 映射，逐成员取用。

**不新增** `load_graph` 调用、**不新增**授权判定——避免二次计算与第二套可见性口径。

### 3.2 方向处理

实测 PFV `edges` 是 **viewer 锚定**的（`from_user_id == viewer.id`）。实现按此为主，并**防御性处理反向**（若出现 `to_user_id == viewer.id`，取该边的 term 给 `from_user_id`），不假设单一方向。

### 3.3 无路径 / 无投影时

- 成员有路径 → `relation_term = <称谓>`；
- 成员无路径（`inclusion_reason_code='space_member'`）→ `relation_term = null`；
- `payload is None`（无投影）→ 一律 `null`。

**不生成占位文案**，避免泄露「是否存在关系」。前端 `null` 时不渲染该行。

### 3.4 字段与兼容

- 成员行新增 `relation_term: str | None`，其余字段与白名单口径不变（`none` 成员仍完全省略，成员列表仍不含 viewer 本人）。
- 前端 `HouseholdCardMember` 增可选 `relation_term?: string | null`；成员卡在 `household_label` 旁按存在性渲染称谓。旧响应无该字段时不报错、不渲染。
- **只改家庭卡**：PFV view payload、家族树画布、Steward 骨架的投影形状一律不动。

## 4. 连带更新（必须同步，避免留下自相矛盾的说明）

| 位置 | 现状 | 处理 |
|---|---|---|
| `dev_seed.py` docstring L18 | 「朱元璋进李家」 | 改为 朱佛女（或删去该条） |
| `dev_seed.py` L233-235 注释 | 「朱元璋只在李家 household…」 | 按新语义重写 |
| `backend/tests/test_dev_seed.py` L322-323、L400-405 | 断言 李家 household 2 人含 朱元璋 | 按新名册更新 |
| `docs/DEV-DATA-SEEDING.md` L99 | 记载李家构成 | 同步 |
| `frontend/src/composables/__tests__/{spaceSelection,useSpaceContext}.spec.ts` | fixture 注释称「朱元璋式数据：最新的 household 是别人拥有的李家」 | 该 fixture 仍是有效的合成场景（我加入的、别人拥有的、排序靠前的 household），但**引用了一个改动后不再存在的种子事实**，需改注为合成场景或用 马府 之类仍成立的例子 |
| `.trellis/spec/frontend/state-management.md` L52 | 引用「09-20 走查实测：朱元璋先显示「李家」」 | 这是**历史走查记录**，描述当时实况，按规范「历史任务保持冻结」精神**不改** |

## 5. 不做的事

- 不改可见性级别、不改 `visibility.evaluate` 抉择链；
- 不把关系称谓加进 PFV/画布/Steward 投影；
- 不让前端推导称谓；
- 不删除或重写 0052/0053 等已应用迁移；
- 不在迁移里删除 `personal_family_views` 行（只标 stale）。

## 6. 风险

| 风险 | 处置 |
|---|---|
| 迁移误删真实用户成员行 | 判定条件收窄到 (空间名, 用户, active, added_by=owner)，并写测试断言「其他空间与其他行不变」 |
| 降级跨 0053 结构迁移产生中间态 | downgrade 先履行父级 preflight（沿用 0052 模式），并加测试（含深层相对目标） |
| 补建的 朱佛女 行破坏幂等 | 用 `WHERE NOT EXISTS` 等价检查，重复 upgrade 不重复插入 |
| R3 让成员字段白名单变化破坏既有前端 | 新字段设可选，前端按存在性渲染；后端测试同步更新白名单 |
