# Agent 双 agent 深度审核修复：运维、闭环与链路

> 父任务：统筹 2026-09-15 深度审核发现的全部修复子任务。
> 状态：执行中。用户硬性要求：**后端等所有服务保持在服务器上运行**（本地只做代码编辑，部署与验证在远端 lyston 服务器完成）。

## Goal

把 2026-09-15 对 assistant / steward 两个 agent 的深度审核发现全部落地修复：消除本地 sidecar 401 死循环、给远端裸进程加守护、让记忆链路有真实输入、打通管家建议的过期与提醒闭环、清理助手链路的空事件与长会话膨胀。

## 审核依据（已核实事实）

- 本地 `.dev-logs/agent-sidecar.log` 自 2026-09-15 起每 ~2.5s 刷 401 `AGENT_TOKEN_INVALID`：本地 sidecar 经 launchd SSH 隧道打到远端 8001，两边 `AGENT_SERVICE_SECRET` 不一致。
- ~~远端 backend 与 sidecar 为裸进程无守护~~ **误判修正**：远端已有 systemd user 级单元（familygraph-api / familygraph-agent）+ linger + code-sync/db-backup 定时器，见子任务 09-15-remote-service-guardian 核查记录。初次审核误查 system 级 unit。
- 远端库：1930 steward jobs 全部 succeeded、300 generations 全部 published（管家核心循环健康）；但 535 条 `steward_suggestions` 全部 `proposed`、`expires_at` 全 NULL、0 条被处理；21 张 action card 全 `pending`；通知 20/21 未读。管家"没起作用"= 产出物沉淀无人消费且无提醒。
- 远端 `memories` / `rag_documents` / `memory_candidates` 全部 0 行：`MemoryCandidateExtractor._default_detector` 默认返回空列表，普通聊天永不产生记忆候选，RAG 检索索引为空。
- assistant run #2 时间线：全程 38s，其中 33s 为模型推理（liu-dada 中转 `gpt-5.6-sol`）；链路开销 <1s。首个 turn 产生一条空文本 `message.assistant_added` 事件仍走完整持久化/SSE；会话历史每轮全量重放无压缩。

## 子任务地图

| 子任务 | 优先级 | 范围 |
|---|---|---|
| 09-15-fix-sidecar-secret-sync | P0 | 本地 .env 与远端 AGENT_SERVICE_SECRET 对齐，本地 lease 验证 |
| 09-15-remote-service-guardian | P0 | 核查记录：远端 systemd 守护已存在（误判修正），无代码改动 |
| 09-15-memory-extractor-onboard | P1 | 规则式记忆候选提取器接入，RAG 有输入 |
| 09-15-steward-suggestion-loop | P1 | proposed 建议 expires_at 落实 + GC 核实 + 未读提醒聚合 |
| 09-15-assistant-latency-optimizations | P2 | 空事件过滤、会话压缩 |

## 跨子任务验收

1. 远端 `systemctl --user status familygraph-api familygraph-agent` 均 active（已确认；重启自愈由 systemd + linger 保证）。steward integrity_scan 继续产出 published generation。
2. 本地 sidecar 日志不再出现 401 轮询。
3. 远端库出现非零 `memory_candidates`（提取器产生真实候选）。
4. `steward_suggestions` 的 proposed 建议具备有限 `expires_at`；GC 后过期建议收敛。
5. assistant 新 run 不再产生空文本 assistant 事件。
6. 全部服务运行在服务器上；本地工作区仅承担代码编辑与提交。

## 部署约束

- 代码经 git 提交后用 `scripts/server-sync-code.sh` 同步到远端（该脚本只同步代码，不迁移不重启——见 CONSTRAINTS #355），随后在远端手动执行迁移检查与重启。
- 密钥只经环境变量/远端 env 文件传递，不写入代码、任务文件或提交。
- SQLite 远端库路径 `/home/ubuntu/projects/FamilyGraph/backend/data/db/app.db`；服务运行期间禁止直接复制主库。
