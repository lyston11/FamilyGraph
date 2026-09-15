# 核验记录：记忆候选提取器

## 核验轮次 1（trellis-check 子代理，commit 506fedd）

结论：有条件通过。真实命令输出：47 passed（目标用例）、ruff/format/mypy 全绿、全量 1623 passed。

发现并已修复（commit 9433d7c）：

| 编号 | 问题 | 处理 |
|---|---|---|
| P1-1 | R1「超出丢弃并计数」未实现 | 新增 `rule_detector_with_stats` 返回 `(inputs, dropped)`，settle 路径 logger.info 记录丢弃数；测试断言 dropped==2 |
| P1-2 | R1 首发类别缺「纪念日」「学校」 | 补 `anniversary`、`school` 规则（校名 ≥3 字），测试覆盖 |
| P2-1 | 延迟到外层 flush 的失败会击穿 settle（终态丢失、run 停在 leased） | 提取整体包在 `db.begin_nested()` savepoint 内；新增反例测试 `test_settle_survives_deferred_flush_failure` 证明 run 仍 succeeded 且无残留写入 |
| P2-2 | 吞错后部分写入被提交、领域事件可能缺失 | 同上 savepoint 修法：失败即回滚到 savepoint |
| P3-1 | 偏好否定语义反转（「不喜欢」→「喜欢」；「哪怕」→「怕」；「不吃亏」→饮食禁忌） | 加否定/习语守卫 `_NEGATION`（不/没/别/无/哪，命中词前 2 字内），dietary 弱信号「不吃」加后接动词屏蔽；测试覆盖 5 个反例 |
| P3-2 | 公共 seam 幂等键在无 `source_message_id` 时跨消息碰撞（`extract:None:...`） | 键改用 `item.source_message_id or source_message_id`；新增 seam 测试 |
| P3-3 | 默认 extractor 版本串不一致 | `MemoryCandidateExtractor.version` 统一为 `memory-extractor-v1` |
| P3-4 | 8000 硬编码 + 超长静默 no-op | `_INPUT_MAX_CHARS` 引用 `config.AGENT_MESSAGE_MAX_LENGTH`；超长消息直接跳过并记日志（不截断，避免原文失配） |
| P3-5 | 缺「settle 失败后无候选」用例 | 新增 `test_settle_failed_produces_no_candidates` |

未采纳：无。全部发现均已修复或明确记录。

## 幂等键覆盖的真实场景与局限（核验要求说明）

- 终态 run 二次 settle → 409，hook 不重跑；
- reaper 仅在未终态时回队，回队后二次 settle 会重跑 hook，幂等键挡住重复；
- 同 `message_id` 的第二个 run（重试/重新生成）命中同一键。
- 局限（design 已接受）：run 一旦终态，reaper 不再回队，「hook 抛错被吞」造成的候选丢失不会自动重试，下一条消息才会再提取。

## 核验轮次 2

修复后命令：

```
cd backend && ruff check .            → All checks passed!
cd backend && ruff format --check .   → 396 files already formatted
cd backend && mypy app                → Success: no issues found in 206 source files
cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/test_memory_extractor.py \
  tests/test_memory_rag_service.py tests/test_agent_queue.py -q   → 53 passed
cd backend && PYTHONPATH=. .venv/bin/python -m pytest tests/ -q \
  -k "memory or rag or agent_queue or settle"                      → 304 passed
```

结论：通过（commit 9433d7c）。
