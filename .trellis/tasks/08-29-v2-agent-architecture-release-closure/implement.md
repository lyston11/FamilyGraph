# V2 Agent 架构收口与发布阻断清零执行计划

> 本文件是执行清单，不是实现授权。任务必须在规划审阅后显式 `task.py start` 才能进入实施。

## 0. 规划与基线门禁

- [ ] 审阅 `prd.md`、`design.md`、`notes.md`、`handoff.md` 和 `research/audit-followup.md`。
- [x] 记录当前 `git rev-parse HEAD`、`git status --short --branch`、Python/Node/Docker 版本；保留其他进程未提交改动。（6f93f6f；py3.12.12/node24；见 notes.md §7）
- [x] 运行当前基线质量命令，分别记录通过项、`ruff format --check backend` 失败项和缺失的 E3 证据。（529 passed；agent_queue.py format 已修；E3 缺失见 notes §7）
- [ ] 在 `task.json` 绑定本任务实现 commit；未有证据的 AC 不勾选。

## 1. Agent/Steward 架构收口（AC-ARC-01/02）

- [ ] 为 Assistant 与 Pi Steward 分别建立 prompt、context projection、tool allowlist 和运行身份合同；删除 Steward 复用 Assistant prompt 的路径。
- [ ] 选定 `StewardJob` 为唯一 canonical Job；禁止生产代码创建 `AgentJob(kind="steward")` 第二队列。
- [ ] 为可选 Pi Steward child run 增加与 `StewardJob` 的一对一关联、继承 scope/policy/lease/取消和统一终态。
- [ ] 保留 `StewardEngine` 的确定性关系、DerivedFact、冲突、推荐矩阵和 ActionCard eligibility；Pi 只能解释/提案，不能写 SourceFact 或发申请。
- [x] 补齐 canonical `StewardJob` 的生产 scheduler/worker 入口；测试直接调用执行器不能替代真实 lease、heartbeat、retry 和终态链路。（maintenance.py 进程内泵 + lifespan 接线 + 双开关；lease/retry/终态经 tick 级回归；heartbeat 随 lease TTL 由 reaper 收敛）
- [ ] 增加双用户双空间、operator negative、Steward 无 private/Web、job crash/retry/lease 和 child run 隔离测试。（crash/retry/lease 已有 test_steward + 新 tick 级回归；child run 未启用 Pi 编排故不适用）

## 2. P1 可见性和网络修复（AC-SEC-01/02）

- [x] 修复 `backend/app/api/graph.py`：最终可见节点集合形成后再过滤边，补隐藏端点 ID/label/creator 的回归断言。
- [ ] 设计并实现 internal listener/Compose 网络隔离；验证宿主、nginx、浏览器和错误 JWT/service token 均无法访问 internal API。（双 listener+共享信号+backend internal:true+静态 IP 绑定+生产禁通配地址已落地；宿主/web 负向连通性 E2E 未验证）
- [ ] 生产配置拒绝默认 service/secret；补 typ、audience、scope、space、run/job、allowlist、TTL 和过期负向测试。

## 3. ProviderGateway 与工具协议（AC-RT-01/02）

- [x] 将 provider 解密、模型选择、readiness、stream、timeout/cancel、usage、错误映射、脱敏和 rotation 收口到唯一 gateway。（agent_provider.resolve_runtime 唯一解密出口→internal context 单路径下发；sidecar 单源消费 projection；复核记录见 notes §9）
- [x] 删除 sidecar 对平行 provider env 的生产依赖；保留开发 stub 但不能覆盖数据库配置。（resolveProvider 仅消费 projection；env providers 仅剩 health 上报）
- [x] 明确 Guard 扩展 runner 的异常处理；Provider 请求边界必须由直接调用或显式拒绝路径保证 fail-closed，不能仅依赖会吞异常的 Pi hook。（guardedStreamSimple 直接 onPayload，HTTP 前抛错三码；notes §9）
- [ ] 统一工具版本快照；修复 kinship v1/v2 的首次写入、重放和冲突语义。
- [ ] 完善后端递归 schema validator 和 TypeBox/frontend snapshot contract，覆盖 min/max、numeric、array、enum、nested、additionalProperties。
- [ ] 以数据库原子占位或等价锁修复 tool_call 并发副作用窗口；覆盖 crash/retry/replay。

## 4. 数据权利、RAG、Web 和错误安全（AC-DATA-01/02）

- [ ] 用成熟 AEAD envelope 替换自制 XOR+HMAC；加入 key id/轮换，解密成功后才消费一次性下载资格，补损坏密文和孤儿文件测试。（AEAD by 并行进程完成并整合验证；解密先于消费已修+回归；孤儿文件测试未做）
- [ ] 给 MemoryCandidate 增加来源 session-space 绑定；补跨空间确认、撤权、删除、tombstone、FTS/embedding 立即失效测试。（跨空间确认回归已补；撤权/tombstone/FTS 失效待验）
- [ ] 修复 Controlled Web DNS/连接 TOCTOU、redirect 逐跳解析和 fetch policy 用途选择；补真实/隔离 E2E。（fetch 用途+连接层 IP 钉扎已修+回归；redirect 保持不跟随 fail-closed；真实隔离 E2E 未做）（fetch 用途选择已修+回归；DNS TOCTOU/redirect/E2E 未做）
- [ ] 统一 client、worker、gateway、settle 的错误 redaction；禁止上游 body、secret、PII 和 URL 凭据进入日志/事件。（sidecar redact.ts + worker settle/log 已接线+单测；backend gateway/客户端侧待做）
- [ ] 修复 provider error 先生成空 assistant event 的事件顺序，确保失败态不会伪装成可用回答。

## 5. 前端隔离与可恢复性（AC-ISO-01）

- [ ] 为 `members.ts`、`spaces.ts`、`actionCards.ts`、agent 相关 store 增加 generation/AbortController 和响应前代际校验。（三 store 代际校验+回归已做；agent store 由并行进程改动中；AbortController 未接）
- [ ] 登出、401/token_version、空间切换、后退、撤权时 abort stream，清理消息、工具结果、citation、草稿和敏感缓存。
- [ ] 在 375×812 和桌面视口完成悬浮助手键盘、焦点、Esc/返回、屏幕阅读标签、reduced-motion、流中断和错误恢复人工记录；截图只用合成数据。

## 6. 空库部署、恢复和 E3 证据（AC-OPS-01）

- [ ] 新建空数据卷应用完整 Alembic 链，构建 api/web/agent，确认 health、internal 端口和 sidecar 无 DB/uploads mount。（空卷 20 迁移链 + 双 listener + sidecar 本机已验；docker compose 栈重建待用户确认）
- [ ] 写入合成双用户双空间、SourceFact、Session/Run/Event、Memory/RAG、ActionCard、Web citation 数据。
- [ ] 执行 SQLite online backup；在第二个新卷 restore，运行 `integrity_check`、关键表计数、外键/约束、SourceFact revision、事件序列和 FTS rebuild。
- [ ] 重启 api/agent，验证 lease、SSE 历史、Last-Event-ID、tombstone、投影重建、Steward job/child run 和一条带 citation 的 Assistant E2E。
- [ ] guga 恢复后重新记录 glm-5.2-fast 成功正文；上游 503 期间只能记录为环境限制，不能标记成功。（经用户指定改用 abrdns/GLM-5.2 已出成功正文+工具调用+egress 审计，见 research/e3-model-loop-evidence.md；guga 原模型仍 503）

## 7. Trellis 证据和最终复审（AC-GOV-01）

- [ ] 运行活动和归档任务的 `task.py validate`，确认 JSONL 研究引用在移动/归档后仍可解析。
- [ ] 每条 AC 回写 `status/commit/command/exit_code/tests/artifact/notes`；未达 E2/E3 保持 partial/blocked。
- [ ] 更新父任务追加式审计附录或关联 notes，修正 `planning`/`in_progress`/`completed`/`commit` 互相矛盾但不删除历史。
- [ ] 运行 backend/agent/frontend lint、type-check、test、build、`git diff --check`；修复 `ruff format --check backend`。
- [ ] 执行 trellis-check 全范围复审；只有所有 P1 关闭、P2 有接受信息且用户确认后才允许归档。

## 验证命令

```bash
python3 ./.trellis/scripts/task.py validate 08-29-v2-agent-architecture-release-closure
python3 ./.trellis/scripts/task.py validate 08-28-v2-audit-remediation

cd backend
ruff check .
ruff format --check .
mypy app
pytest -q

cd ../agent
npm run type-check
npm run lint
npm test -- --run
npm run build

cd ../frontend
npm run type-check
npm run lint
npm test -- --run
npm run build

cd ..
git diff --check
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

跨进程、安全矩阵、空库恢复和 UI 人工验收命令以 `research/verification-protocol.md` 为准；每次执行都必须补完整环境、退出码和产物路径。

## 风险文件与回滚点

| 风险域 | 主要文件 | 回滚/降级 |
|---|---|---|
| Steward 双队列 | `backend/app/services/steward.py`、`agent_queue.py`、`models/agent.py` | 关闭 Pi Steward，保留确定性 engine 和单一 StewardJob |
| internal 网络 | `backend/app/main.py`、`docker-compose.yml`、反向代理配置 | 停 sidecar/Agent flag，不开放公开 internal path |
| Provider | `backend/app/services/agent_provider.py`、`agent/src/session.ts`、gateway | 禁止模型调用，保留 v1 API；不恢复 env 旁路 |
| Visibility | `backend/app/api/graph.py`、visibility facade | 返回最小投影或拒绝，不回滚到隐藏边可见 |
| 导出加密 | `backend/app/utils/secretbox.py`、`data_rights.py`、迁移 | 禁用下载完成态，清理临时文件，保留请求记录 |
| RAG/Web | `memory_rag.py`、`controlled_web.py` | 关闭 RAG/Web flag，不扩大 scope |
| 前端缓存 | `frontend/src/stores/*` | abort 全部旧请求并回退到重新加载，不做乐观合并 |

## 失败处理

- 上游 Provider 503、网络不可用或模型响应为空只能产生明确 failed/blocked 终态，不得伪造 succeeded。
- 任意跨空间、secret/PII、internal 越权或 schema 不确定时 fail-closed，并写安全审计。
- E3 证据缺失时保持 AC `partial`/`blocked`，不通过改文档措辞“完成”。
- 代码实现若发现 PRD 与现有架构合同冲突，退回规划阶段更新 `design.md`/`notes.md`，不得在代码里静默选择另一套语义。
