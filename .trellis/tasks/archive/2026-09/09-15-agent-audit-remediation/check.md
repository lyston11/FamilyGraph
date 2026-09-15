# 父任务最终集成核验（2026-09-15）

5 个子任务全部完成并归档后，对 `prd.md` 的 6 项跨子任务验收逐条核实。
所有证据来自远端 lyston 服务器与本地 main 检出，无模拟。

## 逐条核实

### 验收 1 — 服务守护 + steward 循环持续产出 ✅

```
systemctl --user is-active familygraph-api familygraph-agent → active / active
loginctl show-user ubuntu -p Linger                          → Linger=yes
/api/health                                                  → 200
```

steward 循环（近 8 批，每批 20 job，30 分钟节奏）：

```
2026-09-15 23:25 | 20      2026-09-15 22:25 | 20
2026-09-15 22:55 | 20      2026-09-15 21:55 | 20
```

- `steward_jobs`：**2310 条全部 succeeded，0 条非 succeeded**（无卡住/失败）。
- `steward_generations`：40 条全部 published，最近 `published_at = 23:25:44`。

**关于 generations 计数下降（53→49→40）**：不是循环退化，是 GC 正常收敛。决定性证据：

```
min(id)=1  max(id)=680  count=40        → 已回收 640 行旧 generation
20 个空间 × 每空间 2 条 = 40            → 收敛到稳态（每空间仅保留 current + 最新预览）
max(published_at) = 23:25:44           → 每 30 分钟前移，产出未停
```

`steward_gc._collectible_generations()` 按设计回收「已被更新代取代、manifest 已封存、无 publication 引用、
无 view 引用」的旧 generation（spec `steward-action-card.md` 第 12 条）。id 从 1 直接跳到 676–680 的空洞
即删除痕迹。`steward_jobs` 2310 条全部 succeeded 亦佐证扫描未停。

### 验收 2 — 本地 sidecar 不再刷 401 ✅

```
最后一次 AGENT_TOKEN_INVALID: 2026-09-15T15:00:51.547Z
重启（19:17:54）之后计数: 0
```

历史累计 6113 条全部集中在密钥修复前。修复后至今零新增。

### 验收 3 — 远端库出现非零 memory_candidates ⚠️ 链路已验证，生产数据未产生

**链路端到端已验证**（隔离库 `/tmp/fg-accept3d`，生产库 `.backup` 副本，`DATA_DIR` 隔离）：

```
真实历史消息 '你好，我是谁？'      → 0 candidates   （正确：无记忆价值内容）
真实表述消息 '我爸爸的生日是3月15日，他是医生。' → 2 candidates 已落库
生产库核对: cand=0 mem=0 msgs=6（未被污染）
```

隔离库需加载远端 env 才有 `MEMORY_ENABLED=True`（`platform_feature_configs` 无行 → 走 environment 源）。

**生产 `memory_candidates` 仍为 0 的原因已查明确切，且与链路无关**：

```
agent_runs: id=1 created 05:07Z, id=2 created 07:54Z
提取器部署: 3a486f0 = 2026-09-15T16:25Z
→ 提取器部署后没有任何一次真实 run（最后一次 run 早于部署 8.5 小时）
+ 仅有的 2 条 user 消息原文均为 '你好，我是谁？'（问候语，规则正确地不产候选）
+ journalctl 中无 'memory extraction' 日志（提取器从未在真实 run 上被触发）
```

即：**缺的是「部署后的一次真实会话」，不是链路能力**。用户下一次发起含记忆内容的对话即可看到候选。

### 验收 4 — 已按核查结论修正（原 TTL 要求放弃）✅

见子任务 `09-15-steward-suggestion-loop` 的 `design.md` §0：`expires_at=NULL` + `notify=False`
是 09-14 刻意设计；加 TTL 会因 `steward_terminology.py:522` 历史去重不带状态过滤而永久挡住重建。
实际缺陷是可见性，已修复并部署（`NotificationsView` 现渲染建议投影）。

### 验收 5 — 新 run 不再产生空 assistant 事件 ✅（一条证据缺口如实记录）

- **已实现并部署**：`agent/src/events.ts` 对「正文为空且含 toolCall 块」的 `message.assistant_added` 返回空。
- **证据 A**：隔离端到端 smoke 真实 backend + sidecar + 事件持久化 + SSE → **95/95 pass**（父任务核验时在服务器上复跑，同样 95/95）。
- **证据 B**：远端已构建 `dist/events.js` 含过滤逻辑（`hasToolCall` 出现 2 次），行为探针确认 tool-only → `[]`、有正文工具 turn 与最终回答照常产出。
- **未取得**：真实会话的下一次新 run 事件时间线。需要用户实际发起一次对话；按「不伪造验证结果」原则如实标注为缺口。

**同时修正**：原「会话压缩」要求前提被证伪——Pi 自动压缩已启用并已接线
（`SettingsManager.inMemory()` 默认 `enabled=true, reserveTokens=16384`），
且已归档 spec `assistant-history-restoration.md` 明确要求保留、把 recent-N 截断列为错误做法。

### 验收 6 — 全部服务运行在服务器上 ✅

后端 `familygraph-api`、sidecar `familygraph-agent` 均为远端 systemd user 级服务且 active；
本地检出只承担代码编辑、提交与（前端）dev server。本会话无任何服务迁移到本地。

## 集成质量门（合并后全量复跑）

| 检查 | 结果 |
|---|---|
| `agent`: lint / type-check | clean |
| `agent`: `npm test` | **121 passed** (14 files) |
| `frontend`: lint / type-check | clean |
| `frontend`: `npm test` | **751 passed** (72 files) |
| `frontend`: `npm run build` | ok |
| `backend`: `ruff check` / `format --check` | all passed / 396 files formatted |
| `backend`: `mypy app` | Success: no issues in 206 source files |
| `backend`: `pytest` | **1629 passed, 3 skipped** |
| 端到端 smoke（真实 backend+sidecar+SSE） | **95/95 pass** |

## 集成结论

5 个子任务的改动全部落在 main，无相互破坏；三项原本在审核中提出但被核查证伪的要求
（远端缺守护、term_preference TTL、会话压缩缺失）已按证据修正，未实施错误修复。
唯一未闭环项是验收 3 的「生产库非零候选」与验收 5 的「真实新 run 时间线」，
两者都只差真实用户输入，不构成代码缺陷。

## 部署与清理

- 远端 HEAD = `d60fbd3`，与本地 main 一致。
- 5 个任务 worktree 与 `feat/*` 分支全部删除；残留 `backup/09-15-fix-sidecar-secret-sync`
  经核实无未合并提交（`git log main..branch` 为空），为纯备份引用。
- 临时隔离库（`/tmp/fg-accept3*`、`/tmp/fg-parent-smoke*`）已删除。
