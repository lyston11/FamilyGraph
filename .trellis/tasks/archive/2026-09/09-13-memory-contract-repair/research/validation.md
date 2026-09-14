# A 实施验收

2026-09-13。实施基线 `20d0308`，业务文件位于本任务独立 worktree。用户已批准执行与本地提交；本次验收不包括合并 main 或部署。

## 验收映射

| 验收 | 证据与结果 |
|---|---|
| A-AC1～3 | `test_memory_api_contract.py` 的真实 HTTP 创建、pending、确认、同键重放/异参冲突、提交前序列化回滚与双线程竞争通过。候选不被搜索，确认后真实检索命中；一候选只产生一个 Memory。 |
| A-AC4～5 | 三类来源的正反例、错误角色/locator/revision/sensitivity、跨空间、成员失权与根来源撤销通过；列表、确认重放、检索和补建均保持来源限制。独立检查另复现并修复 legacy 恢复扩大既存 scope。 |
| A-AC6 | 删除原消息/会话后本人已确认 user 快照仍可管理并显示 deleted_snapshot；原来源种类保留，FK/CHECK 不阻塞删除。 |
| A-AC7 | 真实服务开关矩阵与前端四组合通过；Memory 关闭、RAG 开启可检索但不可保存。前端能力/读取失败显示错误并清理失效正文。 |
| A-AC8 | 真实 Alembic 链、FK 开关两态、来源回填/隔离、候选指针保全、重复旧确认非破坏阻断、非空降级拒绝及空库往返通过。前端真实 Axios 序列化和独立异步竞态回归覆盖先前 mock 漏洞。 |

## 最终检查

- 后端：`PYTHONPATH=. .venv/bin/python -m pytest -q`，**1050 passed, 3 skipped**。跳过是既有管理员 break-glass 待实现测试；新增 API/并发/迁移专项 **39 passed**，已包含在全套数内。
- 后端静态：Ruff check、323 文件 format check、183 文件 mypy 全通过。共享依赖时确认 `app.__file__` 指向任务 worktree。首次沙箱运行的两个端口 bind 失败属于环境限制；获准后的同一完整命令全部通过。
- 前端：相关 store、Manager/CitationList、Editor/Axios 回归 **56 passed**；lint、type-check、build 通过。独立检查新增 10 个有失败前证据的竞态回归并修复。
- 真实三 listener smoke：**56/56 通过**，包含 **26 项 Memory 检查**；手工创建→确认→检索→保存授权 RAG→撤销原来源→副本读取遮罩/检索失效→删除全链通过。报告不包含凭据或来源正文。
- `git diff --check` 通过。0041 仅机械格式化，AST 不变，未改主检出现有文件。

详见 [后端独立检查](backend-check.md)、[前端独立检查](frontend-check.md)、[真实 smoke](smoke-boundary.md) 和 [接口交接](api-contract.md)。静态与单元检查由文件所有者执行，主线程消费输出、沿关键来源/迁移代码抽查并独立运行真实 listener smoke；不是把子代理报告直接当作最终判断。

## 剩余边界

未运行无关前端全套、浏览器手工交互或真实模型；已有相关测试、完整前端静态/build 和 HTTP smoke 覆盖本次改变。中文检索、引用认证、索引身份/补建仍由 B/D 实施，A 通过不代表这些问题已经解决。非空降级拒绝是保留来源和确认历史的既定合同。
