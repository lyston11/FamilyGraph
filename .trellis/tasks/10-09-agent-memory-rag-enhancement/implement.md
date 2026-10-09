# 实施计划：记忆模型与 RAG 检索质量强化

> 除 P0 外，各阶段在 PostgreSQL 迁移与 `10-03-pgvector-rag` 的结论明确前不得进入实现。
> 每阶段独立可交付、独立可回滚。

## P0 评估基线 —— 已完成（2026-10-09）

- [x] 中文 golden set `backend/tests/fixtures/memory_eval/golden_v1.json`：20 条合成记忆
      + 15 条检索用例（信息提取 / 时序推理 / 知识更新 / 多会话推理 / 弃答）
      + 11 条提取用例；取代关系与已结束时间窗在 fixture 里显式声明。
- [x] 评测器 `backend/app/services/memory_eval.py`（`memory-eval-v1`）：recall@k、
      citation precision、forbidden hits、弃答正确率、p50/p95 延迟，**不调用任何模型**。
- [x] 报告落 `artifacts/memory-eval/baseline.json`（gitignore，与 migration-proof 同约定）；
      结论摘要落 `research/evidence/p0-baseline.md`。
- [x] 回归门 `backend/tests/test_memory_eval_baseline.py`：contract 层硬门 100%、
      `forbidden_hits` 必须为 0；quality 层记录基线但只防退化。
- [x] 端到端一致性测试：`ContextBuilder` 纳入的来源必须与 `search_rag` 命中一致。
- 基线结论：contract 12/12、quality 2/3；forbidden 0；p50 3.2ms / p95 7.0ms。
- 未做（刻意）：**answer 准确率**需要真实 provider egress，不在本模块内；本模块只评测
  检索层，因为检索是回答的必要条件且可完全离线复现。

## P1 记忆取代语义 —— 已完成（2026-10-09，分支 feat/10-09-agent-memory-rag-enhancement，commit 0951e68c）

- [x] 迁移 `0059_memory_supersede`：六列（`valid_from`/`valid_to`/`superseded_by_id`/
      `supersede_reason`/`superseded_at`/`restored_at`）全部可空；refusal guard 在任何
      DDL 之前；降级在存在取代指针时拒绝。
- [x] `confirm_candidate` 支持显式 `supersedes`（参与 fingerprint，同事务生效）。
- [x] 检索 eligibility 增加取代/有效区间过滤（SQL + 文档状态 + `_rows_to_hits` 复核三层）；
      被取代行仍在管理接口可见（历史可审计）。
- [x] 撤销取代 `restore_memory`：来源仍可读才允许，恢复后原地重新激活投影。
- [x] `POST /memories/{id}/supersede|restore`、`MemoryOut` 新字段、前端卡片取代状态与恢复动作。
- [x] 测试 `test_memory_supersede.py`（16 项）：取代/恢复/非法 reason/反向取代/自我取代/
      跨账户/重复取代冲突/确认失败无部分写入/有效区间过期/四层 mutation。
- [x] 验证：backend ruff + mypy + pytest 2289 passed；frontend lint + type-check + 829 tests passed。
- 未做（刻意）：`source_revision` 变化触发**自动**取代。自动取代需要先有 golden set
  证明「同一来源的新 revision 确实代表新事实」，否则会把「来源更新」误判为「事实变化」。
  留到 P2 与模型提议取代一起做。
- 未做（前置阻塞）：P0 golden set 尚未建立，因此「取代确实改善回答质量」还没有量化证据；
  当前证据是行为级的（旧事实不再进入检索与 context，且四层防护经 mutation 验证）。

## P2 提取精炼与记忆工具

- [ ] `memory_extractor` 规则扩展（亲属关系确认、家族事件、称呼偏好、居住迁移）。
- [ ] LLM 精炼阶段：只处理规则召回结果，输出仍是 pending candidate，失败回退纯规则。
- [ ] `memory_propose` / `memory_search` 工具注册（`required_kind="assistant"`），走既有四道门禁。
- [ ] 成本与超时预算：低优先级、单 run 调用数上界、gateway egress 审计。
- [ ] 测试：LLM 不可用时提取仍产出规则结果、工具不能写 Memory、`memory_search` 授权等价于 `search_rag`。

## P3 hybrid 确定性重排

- [ ] union：词法 + 向量候选按 `chunk_id` 去重，保留两路分数。
- [ ] 确定性重排（RRF + 特征），`rank_version` 版本化；默认关闭直到 golden set 证明提升。
- [ ] 查询计划增量：实体锚定（人名 / 称谓 → person_id）与多查询扩展，别名表保持 closed。
- [ ] 测试：重排不改变授权集合、`rank_version` 回退逐字一致、低选择性下取满 k（filter-then-ANN）。

## P4 分层上下文预算

- [ ] `rag_budget` 分层：L1 骨架 / L2 记忆 / L3 片段 / L4 对话，各自上限与总上限。
- [ ] L3 按句边界截断而非整块丢弃；纳入/排除原因写入 `policy_json` 与 build item。
- [ ] 前端与 `agent/src/context.ts` 的共享包络 fixture 同步（估算器版本化）。
- [ ] 测试：单条长片段不挤掉全部其它命中、预算可解释、旧构建仍可重放。

## P5 chunking 与索引换版

- [ ] contextual chunking（chunk 前附文档级上下文）作为新 `index_version`，A/B 后决定。
- [ ] 复用 `RAGIndexMaintenanceState` 的游标/租约/失败台账做回填；原子指针切换。
- [ ] 测试：换版期间查询不半切换、回填失败不推进游标、旧版本仍可检索。

## 验证与交付

- [ ] backend：`ruff check . && ruff format --check . && mypy app && pytest`（按改动范围取最小充分集）。
- [ ] 隔离库验证显式设 `DATA_DIR`，并核对生产库计数未被污染。
- [ ] 每阶段交付说明写明未运行的高成本检查及原因。
