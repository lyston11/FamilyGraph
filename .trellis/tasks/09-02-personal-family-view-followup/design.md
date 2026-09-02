# PersonalFamilyView 遗留投影技术设计

## 1. 设计边界

本任务把已存在的 PersonalFamilyView 授权快照转化为三个浏览器可消费的最小投影合同：household card、空间统计和 notifications。PersonalFamilyView、SourceFact、SpaceMember、Bridge、VisibilityPolicy、ActionCard 和 DomainEvent 仍是各自领域的真源；新投影不能成为关系事实、权限事实或推荐输入的替代真源。

服务端负责认证、授权、投影构造、版本和审计；前端继续使用现有 API decoder 和 Pinia store。页面不直接读取 `User`、`Relation`、`DomainEvent` 或 ActionCard ORM 数据。

## 2. 授权边界与请求数据流

1. API 通过 `require_authenticated_user` 获取当前 family user/account；system_admin 主体不能进入家庭投影端点。
2. `space_id` 仅作为请求上下文，不作为授权凭据。服务层检查空间存在性、当前 active membership/明确 bridge 授权、PersonalFamilyView viewer/root 绑定、view 状态和当前 VisibilityPolicy。
3. 对已撤权、不可见或不存在的上下文采用既有安全 404/统一错误 envelope，不返回空间存在性、隐藏数量或阻断路径。
4. 服务层读取当前安全快照后用专用 Pydantic schema 投影；任何 `none` 节点/边、不可见端点和未授权字段在序列化前丢弃。
5. 权限收紧以当前查询复核为准，不依赖浏览器缓存是否已清除。

## 3. Household card

### 3.1 数据来源

Household card 只消费当前 household space 的最小授权成员投影和空间元数据。成员行只包含前端合同允许的安全显示字段、稳定的必要标识和允许动作；不得从全局 `/users` 或旧 members API 拼装。

`space_kind` 必须为 `household`。lineage 空间应按现有产品约定返回安全的不可用/404 结果，而不是伪装成 household card。

### 3.2 响应和版本

响应字段固定为：

- `space_id`
- `space_kind`
- `space_name`
- `view_version`
- `computed_at`
- `viewer`
- `members`
- `allowed_actions`

ETag 使用空间 ID、PersonalFamilyView 版本、授权策略版本和投影合同版本计算。命中 `If-None-Match` 且授权仍有效时返回 304；授权失效优先于 ETag 命中并返回安全拒绝。

## 4. 空间限定统计

### 4.1 聚合口径

统计服务以当前授权 PersonalFamilyView 快照为主要输入：

- `node_count` 统计可返回的正式节点；`none` 和不存在的节点没有占位。
- `edge_count` 统计两端均在授权投影内且属于允许关系的边。
- `member_count` 统计当前空间内、对当前 viewer 可见且符合合同的成员投影。
- `relation_distribution` 仅按允许的 `dir_class` 聚合，不返回人物 ID 或阻断原因。
- `pending_action_cards` 只统计当前账号在该空间可见、仍处于待处理状态的 ActionCard。
- `pending_memberships` 只统计当前账号有权看到的空间成员申请/待办，不用隐藏数量代表未授权对象。

### 4.2 非 current 状态

响应始终返回 `status`、`view_version`、`computed_at` 和 `stale_reason`。若存在仍然有效的上一份快照，可使用其授权投影聚合并显式标记 stale；若授权已撤销、快照无法通过当前授权复核或没有安全快照，则返回安全的空聚合/明确未就绪状态，不恢复旧全局统计。

统计 ETag 与 household card 分开生成，包含统计合同版本、空间 ID、view_version、policy_version 和相关待办版本。任何计数来源变化都必须使 ETag 失效。

## 5. Notifications

### 5.1 持久化/投影边界

通知是面向收件人的最小展示投影，不是 DomainEvent 或 ActionCard 的替代品。实现应复用现有 DomainEvent、ActionCard、成员/Bridge/关系领域状态；如当前 schema 没有可保存 `recipient_account_id + space_id + read_at` 的通知投影表，则新增最小 Alembic 表，保存通知类型、空间、收件人、领域引用、脱敏 payload、domain_status、ActionCard 引用、created_at、read_at 和投影 revision。不得保存私人原文或隐藏对象数据。

同一领域事件的通知生成必须幂等；领域对象状态变化由原领域命令负责，通知只引用状态快照或安全摘要。

### 5.2 读取与已读命令

`GET /notifications` 先按当前收件人和 space_id 过滤，再执行当前授权复核。`kind=action_card` 必须验证引用的 card_id/revision 与当前空间和收件人匹配；不匹配的行不返回。

单条 read 和 read-all 使用短事务、条件更新和当前账号过滤。它们只设置 `read_at`，不调用 ActionCard accept/reject/execute，不写领域状态事件。成功响应只包含前端固定的 read result。

### 5.3 脱敏

通知 payload 只允许 title、summary、actor_name、space_name 等已批准字段；每个字段可以是明文、`masked` 或 null。Bridge 管理员通知只显示治理动作和必要空间名，不显示家庭关系、anchor、事实证据或对方空间内容。

## 6. ETag、缓存与失效

所有三个 GET 端点都遵循同一条件请求流程：先验证身份/授权，再计算当前安全版本，最后比较 If-None-Match。ETag 不能成为绕过授权的缓存钥匙。

DomainEvent、membership revoke、Bridge revoke/expire、policy/disclosure 变化和空间删除必须使相关投影版本或授权 epoch 失效。前端已有的 space cache/epoch 机制负责丢弃迟到响应；服务端复核负责阻止旧缓存重新出现。

## 7. 兼容、迁移和回滚

- 不删除或重定义旧 `/api/graph/me`。
- 旧无 `space_id` 统计调用方保持原合同，新的空间合同必须显式使用 `space_id`；若路由实现不能同时保持两者，应以版本化/明确错误避免静默改变旧语义。
- 新通知持久化结构（如确需）使用 Alembic，显式 FK、CHECK、索引和 downgrade；不得用迁移静默删除旧领域事件。
- 采用独立 feature flag/端点启用顺序：先 schema/查询测试，再开放读取，最后开放已读命令。回滚时关闭新投影入口并保留事实与通知事件，不回退到未授权旧 fallback。

## 8. 测试设计

后端覆盖：主体隔离、空间 IDOR、household/lineage 合同、字段白名单、撤权即时隐藏、状态机、统计计数口径、通知收件人过滤、action-card 引用、read/read-all 状态分离、并发已读、ETag/304、未知空间防枚举和 migration。

前端覆盖：decoder 顶层拒绝/单条丢弃、masked/none、space cache、epoch、401/logout、状态展示、不回退旧接口、候选不当作确认关系。推荐边界使用只读 current view fixture，不调用真实推荐触发逻辑。
