# FamilyGraph 全局架构设计 v1.2

> 状态：审计复审后修订（2026-08-25 第二轮“有条件通过”整改）。本文档解决审计记录第三节的 10 个架构问题及复审指出的跨文档合同不一致。
> 决策来源分两层：**锁定决策**（用户确认，见 HANDOFF.md §三）与**审计默认假设**（本文标注 `[AD-n]`，实现前可推翻但需记录）。所有 M0–M4 子任务的 design.md 必须引用并遵守本文。

---

## 1. 身份模型：PersonProfile / Account / ClaimState 分离 `[AD-1]`

锁定决策 A3 的用户体验不变（添加关系 ≈ 创建账号 + 一次性 PIN），但内部概念分离（前两者是实体，Claim 是 users 表上的状态字段而非独立领域对象）：

```
users（PersonProfile 家谱人员档案）
  id, name, gender, birth, death, bio, avatar_path,
  privacy_mode(perpetual|handover), created_by→users.id,
  claim_status(managed|claimed), deleted_at(NULL=存活)
accounts（登录凭据，与档案 1:0..1）
  user_id UNIQUE FK CASCADE, pin_hash, pin_must_change(BOOL),
  token_version(INT), failed_attempts(INT), locked_until(DATETIME NULL)
```

- **每个 PersonProfile 建档即配发 Account**（A3 锁定），但 Account 只是"待认领凭据"：
  - `claim_status=managed`：从未登录。代管人 = created_by（handover 模式）或永久编辑者（perpetual 模式）。
  - `claim_status=claimed`：本人完成首次登录且改过初始 PIN。
- **首登强制改 PIN**：`pin_must_change=true` 时仅放行 `PUT /me/pin`、`POST /auth/logout`、`POST /auth/refresh`（会话延续所需最小集合），其余 API 一律 `403 PIN_CHANGE_REQUIRED`；改毕置 false 且 `claim_status=claimed`、token_version+1。（`GET /api/health` 为公开端点，不经此依赖管辖。）
- 已故/未成年人：自然停留在 managed 态，无需特殊逻辑。冒用风险缓解：PIN 一次性展示 + 审计留痕 + 首登强制换 PIN——本人认领改 PIN 后，**旧初始 PIN 即失效**（持旧凭据的创建者无法再登录冒用）；perpetual 归属模式下创建者的档案编辑权不受认领影响（D5 明确保留的权利，失权的只是旧凭据）。
- 认领不可逆：claimed 后不能退回 managed（v1 非目标：注销/移交）。

## 2. 认证安全合同 `[AD-2]`

| 项 | 规则 |
|---|---|
| 登录限流 | 按 (name, IP) 失败计数，连续 5 次失败锁定该 name 15 分钟（accounts.locked_until），错误文案统一 `用户名或 PIN 码错误` |
| JWT | access 2h + refresh 30d 轮换；refresh 凭据持久化于 `refresh_sessions` 表（user_id FK、token_hash、rotated_from、expires_at、revoked_at） |
| Refresh 轮换与重用检测 | 每次 refresh 将旧行置 revoked_at 并签发新行（rotated_from 链）；提交已 revoked 的 token 视为重用攻击 → 撤销该用户全部活跃会话 + 审计告警 |
| 注销/失效 | 登出 = revoke 对应 refresh_session 行；access 短期自愈。**改 PIN / 重置 PIN / 删除档案 → token_version+1**，校验时比对，旧 access 全部失效 |
| 同名同 PIN 消歧 | 两步：①`POST /auth/login{name,pin}` 多命中 → 写入 `auth_challenges` 表（id/jti、candidate_ids_json、ip、expires_at=5min、used_at NULL）并返回 `409 {challenge_id, candidates[{id,name,created_by_name}]}` ②`POST /auth/login/select{challenge_id,user_id}` → 服务端在单事务内校验未过期且 used_at IS NULL 后原子置 used_at（数据库保证单次使用、防重放）→ JWT；过期/已用一律拒绝并审计 |
| 审计 | audit_log(actor_id, action, target_id, ip, detail_json, created_at)；记录 login_failed≥3、pin_reset、全部 admin 操作、档案删除。保留 ≥180 天，仅 admin 可读 |

## 3. 新用户的家庭空间生成规则 `[AD-3]`

「以我为中心的家庭空间」（默认首页）是**派生聚合视图**，不是新实体：

1. 登录后首页 = 我拥有 active 成员资格的所有空间的卡片**去重并集**，按全局图布局渲染（U1 第一人称）。
2. 空间切换器可查看单个空间。
3. **默认进入空间优先级**：最近活跃的空间 > 我 own 的第一个 > 被拉入的第一个 active 空间。
4. 若我无任何空间成员资格（如被建档时未勾选加入任何空间）：首登引导创建「我的家庭」默认空间（owner=我，初始成员仅自己），随后基于 active 关系给出"一键邀请家人"建议列表——邀请走 D4 正常确认流，**绝不静默拉人入空间**（可见性升级必须经对方同意）。

## 4. 连接与空间成员状态机 `[AD-4]`

### Relation FSM
```
pending ──accept──> active        pending ──reject──> rejected(终态)
pending ──cancel──> cancelled(终态, 发起方)
active  ──revoke──> revoked(终态, 任一方; 断连轨 D8)
约束: 同一对用户最多一条非终态边 (partial unique index);
      自环禁止 (CHECK from_user != to_user); elder 边成环检测拒绝
反向显示: 不存反向行; 展示时结构类反译 elder↔younger / peer,spouse 对称,
      称谓标签始终显示创建者视角原文 [D3]
```

### SpaceMember FSM
```
pending ──accept──> active    pending ──reject/withdraw/expiry(30d)──> 终态
active  ──remove──> removed(终态, owner 或本人)
幂等: UNIQUE(space_id, user_id) 仅一行, 重复申请返回既有 pending
```

### 合并请求（回答"一次申请还是两次申请"）
- **connection_request（M1/M2 主流程）**：一份请求同时携带 `relation{dir_class,label}` + 可选 `space_membership(space_id)`。对方一次接受 → 两者同时 active；拒绝 → 同时取消。消除"关系 active 但空间 pending"的中间权限态。
- **新建账号例外（复审澄清）**：由代管人创建 **managed 新档**时，relation 与可选的 space_membership **直接 active**，不走确认流——创建者即代管人，D4 锁定语义本就如此；只有目标为**已存在或已 claimed 的账号**才进入 pending 合并确认流。
- **join_request（M2 家族视图摘要卡）**：仅携带 space_membership，无新关系边。目标空间 owner 审批。

### 权限授予判定（服务端实时计算，无缓存）
**QU1 已裁定为修订版 U5（2026-08-25，用户确认）**：

完整数据访问 ⇔ **双方在同一空间且均为 active 成员 ∨ 两端点之间存在至少一条 dir_class ∈ {elder, younger, spouse} 的 active 关系边**。

- 直系结构边（亲子/配偶）是信任代理：对端互见完整档案——家谱核心语义是血缘链上的信息可见。
- peer 边对端与仅 clan 连通可达者 → 摘要（名字/称谓/世代）；其余 invisible；搜索遵循同一基线。
- pending 期间：发起方可看接收方摘要（通知需要），反之亦然。断连/移出即时降级。

## 5. 数据库契约

- PRAGMA：`foreign_keys=ON, journal_mode=WAL, busy_timeout=5000, synchronous=NORMAL`（api 启动时统一设置）。
- FK/CASCADE：accounts.user_id、relations.from/to、space_members.user_id/space_id、node_positions、attachments.user_id 均 `ON DELETE CASCADE`。
- 约束：dir_class CHECK 枚举；UNIQUE(accounts.user_id)、UNIQUE(space_members.space_id,user_id)；partial unique index 保证单一非终态关系。
- 布局确定性规则（树状视图）：
  - 多根：各 elder 根并列顶层；
  - 子女归属：由子女自身的 elder/younger 边直接决定（父、母各有独立边），不通过配偶推导；
  - 多配偶：按关系创建序并列同行展示；
  - 冲突/异常数据：布局失败回退画布自由模式并提示（M1 验收项）。

## 6. 授权矩阵（visibility.py 单点实现）

| 资源 \ 主体 | 本人 | 同空间 active 成员 | 直系结构边对端（elder/younger/spouse active） | peer 对端 / clan 连通可达 | 其余 |
|---|---|---|---|---|---|
| 档案详情字段 | full | full | full | summary(name/称谓/世代) | invisible |
| 图节点+关系边 | full | full | full | 仅摘要节点 | 不返回 |
| 头像原图 | full | full | full | 占位图 | 占位图 |
| 附件元数据/下载 | full | full | full | invisible | invisible |
| 搜索命中 | — | — | 允许(full 详情) | 允许(摘要) | 不可命中 |
| 统计聚合 | — | — | 计入范围 | 计入范围 | 不计入 |
| join_request | 目标空间 owner 可见审批 | — | — | — | — |
| 管理 API | is_admin only + audit | — | — | — | — |

- IDOR 集成测试逐行覆盖矩阵（普通 JWT 直打 API 断言遮罩/invisible）。
- 文件下载走授权端点流式返回（禁止 nginx 直链 uploads 目录），响应头 `Content-Disposition` + `X-Content-Type-Options: nosniff`。

#
#
#
 
家
族
空
间
外
披
露
开
关
 
`
[
A
D
-
9
]
`
（
2
0
2
6
-
0
8
-
2
5
 
用
户
裁
定
）




-
 
适
用
对
象
：
非
同
空
间
且
无
直
系
结
构
边
的
家
族
可
达
者
（
p
e
e
r
 
对
端
、
远
房
）
。


-
 
必
要
字
段
始
终
可
见
：
名
字
、
称
谓
标
签
、
世
代
角
标
。


-
 
其
余
字
段
按
*
*
五
个
类
别
开
关
*
*
由
归
属
者
决
定
是
否
在
家
族
空
间
公
开
：
`
a
v
a
t
a
r
`
 
/
 
`
p
h
o
t
o
s
`
(
相
册
)
 
/
 
`
d
a
t
e
s
`
(
生
卒
)
 
/
 
`
b
i
o
`
 
/
 
`
a
t
t
a
c
h
m
e
n
t
s
`
(
链
接
附
件
)
，
存
储
于
 
`
u
s
e
r
s
.
c
l
a
n
_
d
i
s
c
l
o
s
u
r
e
_
j
s
o
n
`
，
*
*
默
认
全
部
不
公
开
*
*
。


-
 
开
关
修
改
权
 
=
 
该
档
案
的
 
D
5
 
编
辑
权
主
体
（
c
l
a
i
m
e
d
 
本
人
；
m
a
n
a
g
e
d
 
档
案
为
代
管
人
）
。
A
P
I
：
`
P
U
T
 
/
u
s
e
r
s
/
{
i
d
}
/
d
i
s
c
l
o
s
u
r
e
`
。


-
 
v
i
s
i
b
i
l
i
t
y
.
p
y
 
在
矩
阵
第
 
4
 
列
（
p
e
e
r
/
c
l
a
n
 
可
达
）
判
定
时
消
费
此
配
置
：
开
放
的
类
别
返
回
 
f
u
l
l
，
未
开
放
返
回
 
M
A
S
K
E
D
 
结
构
；
搜
索
与
统
计
口
径
一
致
。


-
 
直
系
结
构
边
对
端
不
受
开
关
限
制
（
Q
U
1
=
B
：
完
整
互
见
）
。




## 7. 删除语义（实现在 M1，非 M4）`[AD-5]`

- API：`DELETE /users/{id}`。权限：本人 ∨ 代管创建者（perpetual 模式或 handover 未 claimed）∨ admin。二次确认（前端输入名字确认）。
- 单事务级联：关系边删、space_members 删、node_positions 删、attachments 记录删、涉及该用户的 pending 请求删；audit_log **保留**（target 引用改为快照文本）；token_version+1 使其会话即刻失效。
- 物理文件删除在事务提交后异步执行，失败记清扫日志（孤儿文件由 m3a 的清扫任务兜底）。
- v1 采用硬删除，无回收站（HANDOFF 非目标）；备份文件中的残留数据随备份轮转淘汰。

## 8. 备份恢复（修正 WAL 直接复制缺陷）`[AD-6]`

- 备份命令：`python -m app.backup`（容器内执行），使用 **SQLite online backup API**（`Connection.backup`）产出一致性快照至 `/data/backups/familygraph-YYYYmmdd-HHMMSS.db`，随后与 `/data/uploads` 一同 tar 归档。
- 恢复演练是 M4 出口条件：restore 后 `PRAGMA integrity_check` 通过 + 用户数/关系数与源库一致。
- README 写明：**禁止**运行期直接 cp 主库文件。

## 9. 附件安全边界 `[AD-7]`

- 上传校验链：扩展名白名单(jpg/jpeg/png/webp) → Content-Length ≤10MB → magic bytes 校验 → Pillow `verify()` 真实解码 → 最大像素 8000×8000（防解压炸弹）→ 重编码输出（strip EXIF/脚本元数据）。SVG 一律拒绝。
- 外链附件：URL scheme 白名单 http/https；**服务端不抓取外链**（无 SSRF 面），前端 `<a target=_blank rel=noopener>` 外跳。
- 删除一致性：先事务删记录，后异步删文件 + 定期孤儿清扫脚本。
- 依赖新增：Pillow（m3a 引入）。

## 10. 数据权利与威胁模型边界 `[AD-8]`（v1 明确不做部分见 HANDOFF 非目标）

- 未成年人分级隐私：**v2 待定**。v1 依赖 U5 基线 + 家庭信任模型，写入 HANDOFF 默认假设。
- 敏感缓存清理：logout 清空 Pinia state + localStorage(JWT) + 内存中的图数据；路由守卫兜底。
- 数据导出/更正：v1 提供管理员协助通道（admin 数据修正后台），自助导出列 v2。
