# V2 审计整改任务交接摘要

## 当前状态

- 任务：`08-28-v2-audit-remediation`
- 状态：`planning`
- 代码基线：`dc46e34`
- 来源：`08-26-v2-agent-system` 及七个归档子任务
- 本轮只创建规划工件，没有运行 `task.py start`，没有修改产品代码，也没有归档旧任务。
- 当前无真实成员、账号或生产数据，不设计迁移双写/回填/兼容窗口。

## 一句话目标

把 v2 从“代码大体落地但证据和安全边界未闭环”整改为“Provider、权限、事件、数据权利、跨空间隔离、Web egress、UI 和 Trellis 记录均可复现验收的发布候选版本”。

## 必须先读

1. `prd.md`：目标、范围、12 个整改域、18 条 AC 和 DoD；
2. `design.md`：目标拓扑、事务、VisibilityDecision、ProviderGateway、事件和回滚；
3. `implement.md`：按阶段执行、门禁和风险文件；
4. `research/audit-baseline.md`：审计证据、精确代码锚点和严重度；
5. `research/verification-protocol.md`：命令、矩阵、空库恢复和证据格式；
6. `notes.md`：决策、风险和禁止改变的产品边界。

## 发布阻断条件

以下任一项存在时不得把任务标为 completed 或部署真实成员数据：

- 归档后 JSONL 仍有失效引用；
- 建档关系不是原子事务；
- 任一出口绕过字段级 VisibilityPolicy；
- Provider 仍依赖 sidecar 环境旁路或 local-required 静默回云；
- internal API 宿主可达；
- schema/事件/SSE 有重复、漏序或终态缺失；
- 明文导出、secret/PII 日志、跨空间 RAG/Session 命中；
- SourceFact 旁路写入、ActionCard 自动发申请或空间物理合并；
- 375px、空库 Compose、backup/restore 或真实跨进程 E2E 无可复核证据。

## 推荐实施顺序

```text
Trellis 工件/证据治理
  -> 原子建档 + VisibilityPolicy + 导出安全
  -> ProviderGateway + internal 网络 + schema 合同
  -> Event/SSE/lease/audit/redaction
  -> Assistant/Steward/Relationship/Memory-RAG/Web 跨层回归
  -> UI 375px + 空库 Compose + backup/restore
  -> trellis-check + spec 更新 + commit + archive
```

## 下一步

本轮规划资料已齐全，但仍需用户审阅本任务最新 `prd/design/implement` 摘要。只有后续明确批准后，才运行：

```bash
python3 ./.trellis/scripts/task.py start 08-28-v2-audit-remediation
```

启动后按 `implement.md` 顺序执行，所有修改都要记录 commit、命令、退出码和产物。不要启动旧的 `08-26-v2-*` 归档任务，也不要用旧 handoff 的测试数字代替当前 commit 的验证。

## 已知非阻塞选择

Provider 品牌、加密库、internal listener 拆分方式、地区语言包和 embedding 模型尚未指定，但不改变产品行为和安全合同，可在实现阶段记录并提供回滚点。
