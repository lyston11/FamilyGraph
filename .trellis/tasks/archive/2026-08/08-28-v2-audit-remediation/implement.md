# V2 审计整改与发布就绪执行计划

> 本清单是实施顺序，不表示任何步骤已经执行。开始实现前必须完成规划复审，并运行 `python3 ./.trellis/scripts/task.py start 08-28-v2-audit-remediation`。任一 P0/P1 未关闭都阻止归档。

## 0. 进入实施前

- [ ] 用户明确批准本任务最新规划摘要；批准后才允许 `task.py start`。
- [ ] 工作树干净，记录 HEAD、状态、运行环境和当前任务来源。
- [ ] 阅读 `prd.md`、`design.md`、两份 research 与 JSONL 上下文；建立整改分支。
- [ ] 不在旧 v2 归档目录直接改状态；不删除其他进程产生的文件。
- [ ] 将旧 v2 矛盾状态列为待更正审计附录，不把历史规划文档伪装成实现日志。

## 1. Trellis 工件与证据链（G0/G1）

- [ ] 修复 archive-aware JSONL resolver 或历史引用，使活动目录、归档目录和自引用均可验证。
- [ ] 为每个执行 AC 建立 status、commit、command、exit_code、tests、artifact、notes 记录；没有证据的 AC 保持 partial/blocked。
- [ ] 回写父/子 PRD、implement、handoff、notes，使 planning/in_progress/completed 语义一致；采用追加式审计更正。
- [ ] 为真实实现和验证提交绑定 task.json.commit；为残余风险填写结构化 notes。
- [ ] 运行全部 active/archive task validate，并保存完整输出。

检查点：任务工件可从当前 commit 独立读取；无 stale path；旧任务不再误导下一位 Agent。

## 2. Foundation 数据与隐私边界（F1-F4）

### 2.1 原子建档

- [ ] 实现单一 create_managed_member（或等价）命令，补齐名字、关系、placement、描述和幂等 schema。
- [ ] 将 MemberCreateWizard 改为一次 API 提交；删除“关系失败只 warning、档案仍保留”的分支。
- [ ] 保存关系原文；覆盖 managed 新档直接 active 与已有账号 pending 合并分支。
- [ ] 覆盖自环、环、无权空间、非法 placement、重复 key、不同 request hash、中途异常和重试。

### 2.2 VisibilityPolicy 收口

- [ ] 引入不可变 VisibilityDecision/projection facade；禁止 visible ID 后读取原始字段。
- [ ] profile、graph、search、statistics、export、attachment、Agent、RAG 全部消费字段投影。
- [ ] 为 provisional custody 建立最小管理投影；修复 created_by 提前返回 household_detail。
- [ ] operator 用户列表降级为平台元数据；增加 minor/provisional/lineage/guest 统计字段级测试。

### 2.3 数据权利与导出

- [ ] 导出改为 envelope encryption + 短期一次性授权下载；禁止 nginx 直链。
- [ ] 增加 export crash reaper、孤儿临时文件清扫、过期/撤销下载和审计。
- [ ] 验证删除/撤权先 tombstone，再清理 DerivedFact、RAG、附件、session projection 和 export grant。

检查点：Foundation AC-FND-01/02/03 达到 E2；敏感边界和恢复演练达到 E3。

## 3. Provider、网络与工具协议（R1-R3）

- [ ] 实现唯一 ProviderGateway：数据库 provider/secret_ref -> 解密 runtime -> cloud/local call -> usage/error/audit。
- [ ] 删除 sidecar 对平行 provider env 的真实依赖；环境变量只保留开发 stub/bootstrap。
- [ ] 增加 readiness、timeout、cancellation、secret rotation、policy_version、local-required/no-fallback 集成测试。
- [ ] 分离 internal listener 或配置真实私网边界；nginx/宿主访问 /internal/agent 必须拒绝。
- [ ] 生产启动拒绝默认 service secret；验证 token typ、audience、scope、space、run/job、allowlist、TTL。
- [ ] 统一后端 schema 递归校验：min/max length、minimum/maximum、数组、枚举、嵌套、additionalProperties。
- [ ] 建立后端 schema -> sidecar TypeBox -> frontend types 的版本/快照合同测试。

检查点：AC-RT-01/02/03 达到 E2；真实 Compose Provider 与 internal 网络验证达到 E3。

## 4. Event、SSE、审计和错误安全（R4）

- [ ] 删除 sidecar 重复的 message.user_added；补唯一性和 UI 不重复渲染测试。
- [ ] 统一 enqueue/settle/cancel/reaper 的 terminal event 写入、广播和重放。
- [ ] 验证 Last-Event-ID 在 API 重启、sidecar crash、expired/cancelled、重复事件和乱序输入下的行为。
- [ ] 正式副作用工具启用前实现 run_id + tool_call_id + tool_version 去重。
- [ ] 修正 audit actor 的 user/account 语义；增加归属断言。
- [ ] client、worker、provider gateway、settle error 入口统一 redaction；扫描日志、事件和错误响应。

检查点：AC-RT-04/05、AC-OPS-01 的事件和审计部分达到 E2/E3。

## 5. Assistant、Steward、Relationship、Memory/RAG、Web 回归（A1/M1/W1）

- [ ] Assistant 重新执行双用户双空间 Session/SSE/cache 对抗；切换、登出、401、撤权后无旧 scope 残留。
- [ ] 在 375x812 视口完成人工悬浮助手走查，记录焦点、Esc/返回、屏幕阅读标签、reduced motion、错误恢复和合成数据截图。
- [ ] Steward job、ActionCard execute、recommendation matrix 重跑单空间隔离、revision revalidation、并发幂等和 operator negative tests。
- [ ] Relationship golden cases 重跑：奶奶的兄弟、父系/母系、direct sibling unknown parents、收养/继亲、冲突/撤权、跨空间称谓。
- [ ] Memory/RAG 验证未确认候选不可检索、scope predicate 先过滤、删除/tombstone 立即失效、context hook 不查库、Guard 六 hook fail-closed。
- [ ] Controlled Web 重跑双开关、工具披露、SSRF/DNS/redirect/MIME/size、PII/secret、approved token CAS、quota/budget、citation 和 Steward 无 Web。

检查点：AC-ISO-01、AC-KI-01、AC-ST-01、AC-MR-01、AC-WEB-01、AC-UI-01 全部具备当前 commit 证据。

## 6. 空库 Compose、备份恢复与发布门禁

- [ ] 在全新数据卷应用完整 Alembic 链；不使用现有开发库作为“空库”证明。
- [ ] 构建 api/web/agent 镜像，检查 health、graceful shutdown、internal 404、sidecar 无 DB/uploads mount。
- [ ] 写入合成用户、两个空间、SourceFact、Agent Session/Run/Event、Memory/RAG、ActionCard、Web citation。
- [ ] 执行 online backup；第二个新 volume restore，运行 integrity_check、关键表计数、事件序列、SourceFact revision、FTS rebuild。
- [ ] 重启 api/agent，验证 lease、SSE 历史、tombstone、projection rebuild 和带引用 Web E2E。
- [ ] 运行 backend/agent/frontend lint、type、test、build；记录真实命令、退出码、数量和限制。
- [ ] 运行当前任务和相关归档任务的 task.py validate。

## 7. 最终复审、规范和归档

- [ ] 以 trellis-check 口径做 spec compliance、数据流、复用、IDOR、并发、错误处理、脱敏和跨层审查。
- [ ] 值得长期保留的新模式/缺陷预防写入 .trellis/spec/，说明没有扩大产品范围。
- [ ] 逐条回写 AC 结果和证据等级；P2 风险必须有接受者、期限和缓解。
- [ ] 更新 task.json.commit、notes、相关文件和验证摘要；最后一次质量门禁时任务仍为 in_progress。
- [ ] 只有所有 P0/P1 关闭、发布门禁全绿、文档一致且用户确认后，才允许 task.py archive。

## 验证命令

后端：`cd backend && ruff check . && ruff format --check . && mypy app && pytest -q`

Agent：`cd agent && npm run type-check && npm run lint && npm test && npm run build`

前端：`cd frontend && npm run type-check && npm run lint && npm test && npm run build`

部署与任务：`docker compose config`、`docker compose build`、`docker compose up -d`、`python3 ./.trellis/scripts/task.py validate 08-28-v2-audit-remediation`

实际项目若要求 `PYTHONPATH`、特定 `.venv` 或隔离 `DATA_DIR`，必须把完整前缀写入证据，不能以简化命令替代。

## 风险文件与回滚点

| 区域 | 风险文件/模块 | 回滚点 |
|---|---|---|
| Foundation | `backend/app/api/users.py`、`backend/app/commands/*`、`backend/app/services/visibility.py`、`backend/app/commands/data_rights.py` | 独立 migration + flag；失败关闭新入口，不回退越权旧逻辑 |
| Runtime | `backend/app/services/agent_provider.py`、`backend/app/api/internal_agent.py`、`backend/app/services/agent_tools.py`、`agent/src/session.ts` | 关闭 Agent；保留 queue/event 真源 |
| Events | `backend/app/services/agent_queue.py`、`backend/app/services/agent_events.py`、`agent/src/worker.ts` | 暂停新 Run，保留历史事件 |
| Privacy/RAG | `backend/app/services/memory_rag.py`、Policy Guard、stores/composables | 关闭 RAG/Guard/Web 投影，保留 SourceFact/DomainEvent |
| UI | `frontend/src/components/agent/*`、`frontend/src/stores/*`、`frontend/src/composables/*` | 关闭 Assistant 入口，保留会话与事件 |
| Deployment | `docker-compose.yml`、nginx 配置、health scripts | 默认关闭 Agent/Web；基础 API 继续运行 |
| Trellis | `.trellis/tasks/*`、`.trellis/spec/*` | 只追加审计更正；禁止删除历史证据或覆盖其他进程文件 |

## 失败处理

- 测试失败：记录完整输出和环境；修复后重跑同一命令，不用替代命令掩盖失败。
- 端口、Docker、网络或权限限制：标记环境不可复核；补静态/模拟证据，但发布 AC 不得自动通过。
- 新 P0/P1：暂停归档，追加审计基线或 notes，重新评估设计和回滚点。
- 明文 secret/PII、跨空间命中、重复副作用、Provider 静默 fallback 或 SourceFact 旁路写入：立即关闭对应 flag，视为发布阻断。
