# 部署与验证记录：记忆候选提取器

## 部署（2026-09-15）

```bash
git push origin main                              # 3a486f0
ssh ubuntu@lyston 'cd /home/ubuntu/projects/FamilyGraph && git fetch && git checkout main && git pull'
ssh ubuntu@lyston 'systemctl --user restart familygraph-api'
```

结果：`familygraph-api` active、`familygraph-agent` active、`GET /api/health` → 200 `{"status":"ok"}`（注意健康端点是 `/api/health`，不是 `/health`）。
无迁移：本次改动无 schema 变更，未运行 alembic。

## 端到端验证（真实代码 + 真实生产库在线备份副本）

隔离库：`DATA_DIR=/tmp/fg-verify/data`，库由 `sqlite3 .backup` 从生产库复制。

```
isolated db: /tmp/fg-verify/data/db/app.db
[case1] settle=succeeded
  candidates=[('生日：3月5日','normal','private','pending'),
              ('饮食禁忌：她对花生过敏','sensitive','private','pending'),
              ('职业：老师','normal','private','pending')]
  source_quote == full text: True
  extractor_version: {'memory-extractor-v1'}
  idem: ['extract:7:birthday:0','extract:7:dietary:1','extract:7:occupation:2']
  replay -> returned 3, rows still 3        # 幂等成立
[noise] settle=succeeded candidates=[]       # 噪声不产卡
memory.candidate.proposed events: 3
steward_jobs: 2030（与验证前一致，memory.* 不触发 Steward）
```

验收对照：
- AC2 生日候选 pending / summary 含生日 / sensitivity=normal / scope=private ✅
- AC3 过敏内容 sensitivity=sensitive ✅
- AC4 真实链路（真实 settle、真实 agent_message 来源全等校验、真实领域事件）✅
- R3 幂等：重复提取返回同候选、行数不变 ✅
- R6 无迁移、flag 语义不变 ✅

## 事故与清理

首次验证脚本误写生产库：`config.DATABASE_URL` 由 `DATA_DIR` 计算，脚本设置的 `DATABASE_URL` 环境变量被忽略，导致 1 条验证用 user 消息、1 个 run/job、3 条候选、3 条领域事件、2 条 run 事件落入生产库。

处理：
1. 先 `.backup` 到 `/tmp/fg-verify/app.db.before-cleanup` 留证；
2. `BEGIN IMMEDIATE` 事务内按 idempotency_key/policy_version 精确删除这 7 类行（先 SELECT 计数确认命中）；
3. 复核：`agent_messages` 6、`agent_runs` 2、`agent_jobs` 2、`memory_candidates` 0、`memory.candidate.proposed` 0、`agent_run_events` 22 —— 与验证前一致；`PRAGMA integrity_check` = ok；`/api/health` 200；
4. 确认无 Steward 副作用（`steward_jobs` 未增，`memory.*` 事件被 `_schedule_steward_job` 白名单排除）；
5. 用 `DATA_DIR` 指向隔离目录重跑验证，确认生产库不再被写；
6. `rm -rf /tmp/fg-verify` 清除临时文件与备份副本。

教训已写入项目记忆（#358、#359）。

## 未运行的高成本检查

- 前端 `npm run lint/type-check/test/build`：本次改动不涉及 frontend。
- 全量 backend pytest：已跑 `-k "memory or rag or agent_queue or settle"` 304 passed；全量 1623 passed 由核验子代理在修复前跑过。
