# 技术设计：Memory / RAG 平台级开关治理

## 1. 目标与边界

本设计把 Memory 与 RAG 从“只能通过环境变量控制的隐藏部署开关”补齐为“系统管理员可治理的平台级开关”，同时保留家庭端的安全关闭态。两者仍是两个独立布尔能力：Memory 控制候选/记忆读写，RAG 控制检索与索引消费；不把它们改成空间级或个人级设置。

不改动 Memory/RAG 的领域数据模型、VisibilityPolicy、候选确认、scope、FTS 过滤和删除失效机制。系统管理员只接触平台能力元数据，不获得家庭内容。

## 2. 配置与生效语义

### 2.1 新增平台配置单例

新增独立的 `platform_feature_configs` 单例表（建议模型 `PlatformFeatureConfig`）：

- `id`：固定为 1；
- `memory_enabled`、`rag_enabled`：非空布尔值，默认 false；
- `updated_at`；
- `updated_by_system_admin_id`，`ON DELETE SET NULL`。

不复用 `agent_platform_defaults`（它表达模型路由而不是能力开关），也不把字段塞入 `web_platform_configs`（受控联网有独立配置生命周期）。

### 2.2 环境变量兼容与运行时优先级

现有 `MEMORY_ENABLED`、`RAG_ENABLED` 保留为数据库配置尚未建立时的兼容 bootstrap 值，避免旧部署和测试环境在迁移后突然改变行为：

1. `platform_feature_configs` 存在时，以数据库单例的对应字段为平台级产品开关；部署环境变量仍作为不可绕过的 hard-off 兜底，环境为 false 时有效状态必为 false；
2. 单例不存在时，分别回退到 `config.MEMORY_ENABLED` / `config.RAG_ENABLED`；
3. 系统管理员首次读取可返回环境回退状态，但不应静默创建会覆盖环境语义的数据库行；首次写入时创建单例并持久化两个字段的明确值；
4. 管理端分别展示两个能力的来源：环境回退、平台配置或部署级关闭；部署级关闭时对应开关不可在 UI 中强行打开。

这样既保留已有部署的默认关闭和向后兼容，也让管理员完成一次后台设置后不再依赖 shell。若未来需要强制部署级 emergency kill switch，应另设明确的 hard-off 配置，不复用已成为平台运行时真源的两个产品开关。

### 2.3 服务层统一读取

新增 `backend/app/services/platform_features.py`，集中提供：

- `get_platform_feature_state(db)`：返回两个能力的有效状态及来源元数据；
- `is_memory_enabled(db)` / `is_rag_enabled(db)`：供领域服务和 API 门禁使用；
- `set_platform_feature_state(db, memory_enabled, rag_enabled, admin_id)`：事务内 upsert，返回安全投影。

`memory_rag.py` 与 `api/memory.py` 的关闭门禁统一改为消费服务层状态；RAG 搜索门禁同样改为消费服务层状态。不能在调用方重新读取环境变量或直接读取模型。

## 3. API 合同

### 3.1 家庭域只读状态

新增认证后的家庭 listener 端点，例如 `GET /api/platform-features`：

```json
{
  "memory": {"enabled": false},
  "rag": {"enabled": false}
}
```

家庭响应只暴露能力是否可用及安全的稳定状态码/字段，不暴露系统管理员身份、配置审计、环境变量名、数据库来源或部署路径。端点本身不受 Memory/RAG disabled 门禁影响，便于关闭态页面获取状态。

### 3.2 系统管理员读写

新增独立 admin listener 端点，例如：

- `GET /admin-api/v1/platform-features`：读取两个平台级开关及安全来源/更新时间投影；
- `PUT /admin-api/v1/platform-features`：全量写入 `memory_enabled`、`rag_enabled`，请求 schema `extra='forbid'`，必须是明确布尔值。

路由只使用 `AdminPrincipal` / `require_admin_ready`，家庭 JWT 一律拒绝。写入在同一事务中调用 `admin_audit.record_access`，审计只记录两个布尔结果、动作和管理员 ID，不记录家庭内容、Provider secret 或环境变量值。

不要把写端点挂在家庭 `/api`，也不要让 `platform_operator` 家庭身份依赖绕过独立 system-admin 认证。

### 3.3 实施对齐合同（2026-09-13）

- 家庭 GET 精确响应：`{memory: {enabled: boolean}, rag: {enabled: boolean}}`。
- admin GET/PUT 响应：`{memory_enabled: boolean, rag_enabled: boolean, source: 'environment' | 'platform', updated_at: string | null}`；写请求只包含两个必填 strict boolean。
- admin 开关翻动即保存 PUT，再 GET 重同步；同时禁用两个开关直到保存/重读完成，避免整行 PUT 互相覆盖；失败重新确认当前服务器状态，不显示假成功。
- 独立组合：仅 RAG 开时仍允许已授权知识检索，但“保存为记忆候选”依赖 Memory，必须隐藏；关闭 Memory 不应清空已存在的合法 RAG 来源或改变检索授权。
- 所有旧 flag 调用点须沿共同链替换，包括 `ContextBuilder`，不能只更新浏览器 API 导致 Agent 继续消费旧 env。
- SQL/config 读取失败不能解释为“没有配置行”并回退 env；不新增进程全局缓存，否则跨 listener 保存无法生效。
- MR-7 按已批准的 §2.2 优先级落实：保留默认关闭、错误 fail-closed 和其他现有安全门禁，标明环境回退/平台配置来源；不把旧 env 与数据库 AND，否则后台仍无法开启。未承诺的新 emergency hard-off 不在本次实现内。

## 4. 家庭端数据流与 UX

### 4.1 Memory 页面首屏

`MemoryView` / `MemoryManager` 首先读取平台能力状态，然后按状态决定是否发起候选、记忆、共享记忆请求：

- Memory 关闭：展示明确的“记忆功能尚未启用，请联系系统管理员”状态；隐藏新增、确认、忽略、撤销、删除等操作；刷新和返回保持可用；不因 disabled 状态产生一串无意义 503。
- Memory 开启：沿用现有候选和记忆加载路径。
- RAG 关闭：在“检索与引用”分区展示独立的 RAG 未启用状态，隐藏搜索和保存到候选的操作；其他已开启的 Memory 分区仍可工作。
- RAG 开启：沿用现有检索与引用路径。

`AppShell` 可继续保留“记忆与知识”导航，使用户能看到解释页；不能通过前端隐藏来代替权限控制。真正的写 API 仍保留服务端 disabled 门禁，防止旧客户端或伪造请求绕过 UI。

### 4.2 错误分类

平台状态端点成功后，disabled 由状态字段驱动；状态端点 404/网络错误则展示“能力状态暂时无法确认”的可重试错误，不误报为“Memory 未开启”。Memory/RAG 业务 API 的 503 仍保留为后端最终防线，并由前端按 code 分别显示对应能力的安全文案。

## 5. 系统管理员端 UI

新增“平台能力”管理页和 admin 路由/导航入口，或在不破坏现有模型治理页边界的情况下复用其页面框架；优先独立页面，避免与正在进行的 Provider 配置改版产生文件耦合。

页面包含两个独立开关卡片：

- Memory：当前状态、说明、切换控件、保存/成功/失败反馈；
- RAG：当前状态、说明、切换控件、保存/成功/失败反馈。

页面加载和保存均消费 admin API，刷新后重新读取服务器状态。切换前要求明确的启用/停用反馈；不能以本地 optimistic 状态作为最终真源。页面不展示家庭记忆内容或候选原文。

移动端沿用 AdminShell 的可达性规则：开关有标签、当前状态有文本，不依赖颜色；失败时保留重试入口。

## 6. 数据库、迁移与回滚

- 新迁移只创建 `platform_feature_configs`，不改 Memory/RAG 领域表；默认值为 false，符合现有生产默认关闭。
- 迁移不强行插入单例行，从而允许已有 `MEMORY_ENABLED` / `RAG_ENABLED` 环境配置继续作为未配置数据库时的回退。
- downgrade 删除该单例表前应确认不丢弃其他领域数据；回滚后服务自然恢复为环境变量语义。
- 使用临时 `DATA_DIR` 执行 `alembic upgrade head` 和 downgrade 验证，不能触碰开发主数据库。

## 7. 关键风险与取舍

- **配置来源混合**：数据库行建立后环境变量不再覆盖，必须在管理端和文档标明来源；这是让后台开关真正可用的必要取舍。
- **状态与业务请求竞态**：状态读取和业务请求之间可能发生切换；业务端 503 门禁必须保留，前端不能假设状态永远不变。
- **跨任务文件冲突**：Provider 治理任务正在进行，平台能力使用独立后端模块、admin API、admin 页面和家庭 Memory 页面，尽量不修改 `AgentProviderAdminView.vue`。
- **只读状态端点泄露**：只返回两个能力状态，不返回部署细节、管理员信息、数据计数或内容。
