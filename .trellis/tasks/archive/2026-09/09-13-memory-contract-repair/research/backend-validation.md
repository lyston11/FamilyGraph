# A 后端实施核验记录

核验日期：2026-09-13。以下均为本次后端实施阶段已经完成的结果；本文件落盘时没有重新测试或改动业务代码。

工作区：`/private/tmp/familygraph-memory-rag/09-13-memory-contract-repair`。
分支：`feat/09-13-memory-contract-repair`。以下命令的工作目录均为该 worktree 的 `backend/`。

## 最终结果

| 检查 | 实际命令 | 结果 |
|---|---|---|
| 完整后端测试 | `PYTHONPATH=. .venv/bin/python -m pytest -q` | **1050 passed, 3 skipped, 4 warnings，41.75 秒**；经授权在沙箱外完成 |
| 新增 API / 并发 / 迁移专项 | `PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_memory_api_contract.py tests/test_memory_source_migration.py` | **39 passed，6.72 秒** |
| Ruff | `.venv/bin/ruff check .` | All checks passed |
| 格式 | `.venv/bin/ruff format --check .` | 323 files already formatted |
| 类型 | `PYTHONPATH=. .venv/bin/mypy app` | Success: no issues found in 183 source files |
| 差异空白检查 | `git diff --check` | 退出码 0，无错误 |

3 个 skip 均来自既有 `tests/test_m4b_admin.py`，原因是管理员 break-glass 家庭数据能力另立任务、当前端点没有可执行主体。4 个 warning 来自既有 `test_admin_agent_latency.py` 的 Python 3.12 SQLite datetime adapter 弃用提示。

## 工作区导入与沙箱条件

worktree 的 `.venv` 链接到主检出环境，最初直接运行 `.venv/bin/pytest` 时，editable install 导入了主检出的旧 `app`，出现新 `source` 参数不存在的错误；该次结果不作为本轮实现的验证依据。

改用 `PYTHONPATH=. .venv/bin/python -m pytest` 后，实际确认 `app.services.memory_rag.__file__` 为：

```text
/private/tmp/familygraph-memory-rag/09-13-memory-contract-repair/backend/app/services/memory_rag.py
```

完整后端测试首次在沙箱内运行时为 **1048 passed, 2 failed, 3 skipped，40.10 秒**。两项失败均为 `tests/test_serve_bind_plan.py` 的本地端口绑定测试，报 `PermissionError: [Errno 1] Operation not permitted`：

- `test_probe_bind_rejects_active_listener`
- `test_probe_bind_allows_reused_port_without_listener`

按工具要求申请并获得沙箱外执行授权后，重跑同一完整命令，最终 **1050 passed, 3 skipped**。没有通过跳过这两项测试掩盖环境限制。

## 迁移验证

实际起点为 `0041_term_pack_expansion`，新增迁移为 `0042_memory_source_contract`。专项使用合成隔离数据库及真实 Alembic 链，覆盖：

- SQLite `foreign_keys=ON/OFF` 两种连接状态；重建后原 Memory、候选关联、正文、scope、确认历史均保留。
- 本人原始 user 消息且原话完全一致的旧记录可核验；任意文档标签、Assistant 消息、伪原文和跨空间旧 Memory 保持 `legacy/unverified`。
- 删除原消息或会话后外键正确置空，来源种类与审计定位保留，不触发旧来源 CHECK 故障。
- 存量重复确认在迁移变更前明确阻断，不自动删除或改写记录。
- 非空数据库降级明确拒绝，保留来源与确认历史；空库升级、降级、再升级通过。
- 专项发现并修复了 Alembic CLI 与直接 Operations Context 的命名约定差异，约束名使用固定物理名称，最终两种执行方式均通过。

## API、来源与并发验证

真实 FastAPI 请求覆盖显式 manual 创建、`raw_quote` 映射、候选列表、确认、忽略、检索和请求重试。旧缺来源请求、任意文档字符串与冲突来源字段被拒绝。

创建、确认、忽略、撤销四条写路径均注入响应序列化失败，验证服务已 flush 后仍在 commit 前回滚，避免“已提交但响应 500”。

创建竞争的同步点放在两个请求均完成来源解析之后；确认竞争的同步点放在两个请求均读取 pending 并完成首次授权之后，实际覆盖唯一键冲突与条件更新竞争。最终仅有一个候选、一个 Memory 和对应的一次 DomainEvent。确认还覆盖首次授权与取得 writer 之间的目标空间成员失权，以及 Memory / RAG 开关变化。

来源权限覆盖：本人原始 user、他人消息、Assistant/system/派生消息、错误原话、RAG document/chunk/revision/index_version、跨空间、私有来源和敏感度降级。来源撤销、到期、读者成员资格丢失、作者可见性变化均贯穿候选、Memory、幂等重放、RAG 和 Context。已确认本人原始 user 快照可在删除聊天后保留；未确认候选失去原消息后不可确认。

独立 review 复现了旧 `verify_legacy_source` 能把来自 A 空间的旧 Memory 恢复为 B 空间可读的漏洞。已在 apply 前验证既有 scope/sensitivity，拒绝时不修改原记录；永久负例覆盖跨空间消息、高敏感共享和私有 RAG 扩大共享。所有相关测试已包含在最终通过结果中。

Memory/RAG 四种开关组合均经真实 API 验证。RAG 人工阅读与模型本地 Provider 限制分别执行；人工读取可保存当前获权的高敏感来源，模型检索仍遵循本地 Provider 约束。

## 交接与范围

`memory_sources.source_lifecycle` / `memory_materializable` 判断来源生命周期，供 D 的物化维护复用；`document_readable` / `source_access` / `memory_access` 判断当前读者及完整来源链，供 B 的检索与引用复用。接口字段见 [api-contract.md](api-contract.md)。

中文检索算法、索引身份稳定、同版本 tombstone 防复活及后台补建仍由 B/D 负责。本次没有连接开发或线上数据库、调用真实模型、部署或修改运行开关。主线程独立完成的 listener smoke 不计入本代理上述测试计数。

后端业务文件在完整测试通过后已冻结；提交、集成和任务归档由主线程统一执行。
