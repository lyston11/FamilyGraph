# 实施计划：Memory / RAG 平台级开关治理

## 变更顺序

### 1. 后端平台能力配置与迁移

- [x] 新增 `PlatformFeatureConfig` 模型及 `platform_features` 服务，集中实现 singleton 读取、环境兼容回退、有效状态投影和事务内 upsert。
- [x] 新增 Alembic 迁移 `backend/migrations/versions/`，创建平台配置单例表，默认关闭，不改 Memory/RAG 领域表。
- [x] 新增安全 schema：家庭只读状态、admin 读写请求/响应；对请求字段启用严格 extra 校验。

### 2. 后端 API 与门禁替换

- [x] 新增家庭域 `GET /api/platform-features`，只返回 Memory/RAG 安全状态，不受两个能力的 disabled 门禁影响。
- [x] 新增独立 admin 域 `GET/PUT /admin-api/v1/platform-features`，使用 system-admin 认证、权限校验和现有 admin audit。
- [x] 将 `api/memory.py`、`services/memory_rag.py` 中的 Memory/RAG 门禁改为服务层统一读取；保留业务 API 的 fail-closed 503。
- [x] 补充 admin API 路由挂载、错误码/状态合同和配置来源说明；确认不把家庭内容带入 admin 响应或审计。

### 3. 家庭端关闭态与状态加载

- [x] 新增家庭 API client、类型和必要的 Pinia 状态，先读取平台能力，再按 Memory/RAG 状态加载对应数据。
- [x] 修改 `MemoryManager.vue`：Memory 关闭时隐藏所有 Memory 写操作；RAG 关闭时只在检索分区展示独立关闭态并隐藏检索/保存操作；保留安全刷新/返回。
- [x] 区分状态端点不可用、Memory disabled、RAG disabled 与普通网络/服务错误；避免把任意失败显示成“功能未开启”。
- [x] 保持导航和既有开启态布局/交互兼容，不把前端隐藏当作服务端权限控制。

### 4. 系统管理员端入口

- [x] 新增平台能力 admin API client、运行时解码类型和“平台能力”管理页。
- [x] 在独立 admin router/AdminShell 增加入口；页面提供 Memory、RAG 两个独立开关，读取与保存均以服务端响应为真源。
- [x] 补充加载、保存成功/失败、移动端可达性和无家庭数据展示约束。

### 5. 自动化回归测试

- [x] 后端测试覆盖配置回退/优先级、迁移、家庭状态端点、admin 读写、非 admin 拒绝、审计白名单和四种开关组合。
- [x] 后端测试覆盖 Memory/RAG 各自门禁仍独立生效，旧 Memory/RAG 开启态路径不回归。
- [x] 前端测试覆盖关闭态不展示误导性操作、Memory/RAG 独立状态、状态端点异常分类、admin 页面读写和刷新后服务端真源。

## 验证命令

先运行最小相关检查：

```bash
cd backend
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/python -m pytest -q tests/test_memory_rag_service.py <新增或受影响的测试文件>
```

```bash
cd frontend
npm run lint
npm run type-check
npm test -- --run <受影响的 Memory 测试文件>
```

独立管理端单独验证（不是家庭前端测试目录）：

```bash
cd system-admin-frontend
npm run lint
npm run type-check
npm test -- --run <受影响的平台能力/API/路由测试文件>
```

迁移使用隔离数据目录验证：

```bash
cd backend
MIGRATION_DATA=$(mktemp -d /tmp/familygraph-memory-migration.XXXXXX)
DATA_DIR="$MIGRATION_DATA" .venv/bin/alembic upgrade head
# 只回退本任务新增迁移：以前一 head（实施前确认）为目标，禁止跨领域回退到 0014。
DATA_DIR="$MIGRATION_DATA" .venv/bin/alembic downgrade -1
DATA_DIR="$MIGRATION_DATA" .venv/bin/alembic upgrade head
```

最后按范围运行完整检查：

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app && .venv/bin/python -m pytest
cd frontend && npm run lint && npm run type-check && npm test && npm run build
cd system-admin-frontend && npm run lint && npm run type-check && npm test && npm run build
```

## 风险文件与回滚点

- 高风险后端：`backend/app/api/memory.py`、`backend/app/services/memory_rag.py`、`backend/app/main.py`、新平台配置模型/服务/API/schema、迁移文件。
- 高风险家庭端：`frontend/src/components/memory/MemoryManager.vue`、`frontend/src/views/MemoryView.vue`、新 API/store/types。
- 高风险 admin 端：`system-admin-frontend/src/router/index.ts`、`AdminShell.vue` 及新平台能力页面/API/types。
- 当前工作树已有其他任务未提交改动；实施时只修改本任务涉及文件，不重置或覆盖其他改动。
- 若状态合同或迁移验证失败，先回滚平台配置/API/UI新增，保持原环境变量门禁和旧页面行为，再重新收敛设计；不得删除或改写 Memory/RAG 数据表。

## 开始前检查点

- [x] 用户已于最终规划后回复“执行”，批准本次实施。
- [x] 已保存本任务实施前的工作区状态及 diff 到 `/tmp/familygraph-memory-pre-status.txt`、`/tmp/familygraph-memory-pre-diff.patch`；Provider 页面和管理员会话配置已有改动，不覆盖。
- [x] backend/frontend 索引和实现规范已读取并注入；按新模块 + Memory 共同调用链 + 独立 admin 页面分工，界面沿用既有主题/壳，不重设计。
- [x] 完成桌面及 375px 移动端真实渲染走查、路由切换、刷新、开启/关闭/失败回弹；以组件渲染测试、AdminShell 移动导航测试和两端 production build 完成可运行验证（当前环境未提供截图浏览器）。
