# V2 Agent 架构收口任务交接摘要

## 当前状态

- 任务：`08-29-v2-agent-architecture-release-closure`
- 父任务：`08-28-v2-audit-remediation`
- 状态：`planning`
- 本轮动作：只新增后续任务规划工件，没有启动任务、没有修改产品代码、没有归档父任务或旧任务。
- 复审基线：当前工作树 HEAD `6f93f6f93e88cfd5582d976e042fe45676c88e50`，存在其他进程未提交改动；实现时必须保留并重新记录当前 commit。

## 一句话目标

统一 Assistant/Pi/Steward 的真实架构，关闭 P1 可见性、internal 网络、Provider、工具协议、导出、RAG/Web、缓存和发布证据缺口。

## 关键裁定

1. Assistant 是完整 Pi SDK sidecar：`pi-coding-agent -> pi-agent-core -> pi-ai`。
2. Steward 是“确定性 StewardEngine + 可选受限 Pi Orchestrator”；图算法、权限、推荐矩阵、FSM 和正式写入永远由后端决定。
3. `StewardJob` 是唯一 canonical 空间作业；禁止 `AgentJob(kind="steward")` 形成第二套活跃队列。Pi Steward 如启用，只能是关联 child run。
4. Pi 不直连数据库，不开放 SQL/shell/file/MCP/unrestricted HTTP；Steward 不读 private Session/Memory，也没有 Web tool。
5. guga 持续 503 只能记录环境限制；没有成功正文证据就不能关闭发布 AC。

## 已完成/部分完成/未完成

- 已完成：RAG 关闭降级、token 脱敏白名单、`max_tokens` wire 兼容、Provider stream error fail-closed、Assistant Pi Session 基础链路。
- 部分完成：原子建档、ActionCard、Memory/RAG、Provider DB 配置、Deterministic Steward、前端 Agent 分区。
- 未完成/P1：隐藏关系边、internal listener、唯一 ProviderGateway、工具 v1/v2 重放和并发去重、成熟 AEAD、RAG session-space、DNS TOCTOU、错误统一脱敏。
- 未闭环/P1/P2：Steward Pi 生产入口、前端旧请求竞态、ruff format、空库 Compose/restore/FTS/SSE/优雅停机/375px/guga 成功证据、父子 Trellis 工件一致性。

## 推荐执行顺序

```text
规划复核
  -> Steward 唯一 Job/专用 Pi 运行边界
  -> graph/internal 网络 P1
  -> ProviderGateway/schema/幂等
  -> 导出/RAG/Web/错误安全
  -> 前端缓存隔离
  -> 空库/恢复/跨进程 E3
  -> Trellis-check、spec 更新、commit、用户确认、归档
```

## 启动约束

规划工件已齐全，但必须由用户在后续消息明确批准最新规划后，才允许：

```bash
python3 ./.trellis/scripts/task.py start 08-29-v2-agent-architecture-release-closure
```

在此之前不得将 AC 勾选为完成、不得把父任务改为 completed、不得归档。
