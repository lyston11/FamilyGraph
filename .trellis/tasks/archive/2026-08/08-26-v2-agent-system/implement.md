# FamilyGraph v2 Agent 系统执行编排

> 本文件只编排七个子任务，不直接实现产品代码。父任务通常不执行 `task.py start`；按顺序激活拥有实际交付物的子任务。

## 0. 启动前总门禁

- [x] 用户已审阅父任务及七个子任务的 PRD/design/implement，并在新的消息中明确批准进入实现。
- [x] 所有 `implement.jsonl` / `check.jsonl` 至少包含一条真实 spec/research 条目，`task.py validate` 全通过。
- [x] `.trellis/spec/architecture.md` 新增 v2 合同或由 V2.0 任务明确负责更新；不能让 v1 U5 与 v2 VisibilityPolicy 同时作为权威规则。
- [x] 选择实际首个子任务 `08-26-v2-0-foundation`，不要启动父任务。

## 1. 阶段顺序与出口

1. **V2.0 Foundation**：先完成身份、空间种类、确档、VisibilityPolicy、数据权利和领域命令抽取。其迁移与授权矩阵通过后才能接 Agent。
2. **V2.1 Agent Runtime**：接 Node Pi sidecar、内部协议、持久化 Run/Job/Event、ProviderGateway 和 SSE；以假领域工具验通运行时。
3. **V2.2 Read-only Assistant**：只开放读工具，完成单空间会话和全局悬浮 UI；零写入、零跨空间是出口。
4. **V2.3 Relationship Intelligence**：实现 SourceFact/DerivedFact、路径计算、TermRegistry 和解释；确定性引擎通过 fixture 后再接 LLM 解析。
5. **V2.4 Steward & ActionCard**：接后台 Job、DomainEvent、推荐矩阵与卡片确认执行；任何申请发送由用户动作触发。
6. **V2.5 Memory & RAG**：接确认记忆、作用域索引、ContextBuilder 和 Policy Guard；完成删除/撤权传播测试。
7. **V2.6 Controlled Web**：最后启用默认关闭的联网工具，完成 egress、引用、部署和全量回归。

## 2. 每阶段通用执行循环

- [x] 激活唯一子任务：`python3 ./.trellis/scripts/task.py start <child>`。
- [x] 按子任务 implement.md 分小步实现，数据库 schema、后端服务、API、前端状态、UI 和测试保持纵向切片。
- [x] 每次领域写操作审查事务、幂等、审计、可见性和撤权后的缓存失效。
- [x] 执行子任务专用验证命令以及下列通用门禁。
- [x] 使用 Trellis check 全量复审后再提交、归档该子任务；下一子任务不得提前启动。

## 3. 通用验证命令

```bash
cd backend && pytest
cd backend && mypy app
cd frontend && npm run type-check
cd frontend && npm run lint
cd frontend && npm test
cd frontend && npm run build
docker compose config
docker compose up --build
```

Node sidecar 引入后补充并锁定实际脚本：

```bash
cd agent && npm run type-check
cd agent && npm run lint
cd agent && npm test
cd agent && npm run build
```

## 4. 集成复审矩阵

- [x] 用户 A/空间 1 的 Session、消息、RAG 与工具结果不会被用户 A/空间 2 或用户 B 读取。
- [x] Steward 只能看到其 `space_id` 的确认事实与共享知识，不继承平台运营者可见性。
- [x] provisional、pending、guest、minor、disputed、revoked 逐一覆盖授权矩阵。
- [x] 断线续传、重复 Idempotency-Key、sidecar crash、Provider timeout、tool timeout 不重复副作用。
- [x] SourceFact 无 Agent 直写路径；accepted ActionCard 仍可因权限/证据变更被拒绝。
- [x] 登出、空间切换、撤销成员、删除/更正后清理消息缓存、DerivedFact、RAG 与导出投影。

## 5. 回滚点

- V2.0 schema/权限变更独立提交；若授权矩阵失败，回滚整个 Foundation，不用 Agent 层补丁绕过。
- V2.1 sidecar 由功能开关隔离；故障时关闭 Agent 路由，v1 领域功能继续可用。
- V2.2–V2.5 各能力均以服务端 feature flag 分开，禁止通过回滚数据库真源来禁用投影功能。
- V2.6 联网开关默认为 false；任何 egress 缺陷立即全局关闭，无需停用本地 Agent。

## 6. 最终发布门禁

- [x] 空数据库完成全部迁移、首启和完整 E2E。
- [x] SQLite online backup + uploads + Agent/Memory/RAG 数据恢复演练通过。
- [x] 无真实数据迁移脚本、兼容双写或旧关系回填残留。
- [x] 后端、前端、Agent sidecar、Compose、SSE 重连、隐私矩阵和数据权利全绿。
- [x] `.trellis/spec/`、Obsidian 架构文档与实际实现同步更新。
