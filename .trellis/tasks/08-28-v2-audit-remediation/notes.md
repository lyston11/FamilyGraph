# V2 审计整改注记

## 1. 决策记录

### D-01：这是一项整改任务，不重开 v2 产品规划

审计确认 v2 的双 Agent、空间隔离、称谓、Memory/RAG、受控 Web 和 Provider 分层原则仍然成立。本任务只修复实现、证据和交接治理缺口；任何改变产品行为的提案必须另开 PRD 或在本任务中明确追加决策。

### D-02：状态证据优先于 handoff 声明

`task.json=completed`、PRD AC 未勾选、implement 未闭环或 handoff 仍写 planning 时，不能推断“应该已经完成”。关闭 AC 必须绑定当前 commit、完整命令、退出码、测试断言和产物；旧摘要只能作为线索。

### D-03：原子建档是领域命令，而非前端编排

名字和关系是一个产品动作。前端可以分步展示表单，但后端必须在一个短事务内校验、写入、审计和发事件；不能保留“关系失败但档案已存在”的中间结果。

### D-04：VisibilityPolicy 先硬收紧，再叠加 custody/disclosure

`created_by`、同空间、直系关系、operator 身份都不能绕过 provisional、minor、deleted、masked 和高敏感字段 overlay。统计、Agent、RAG、导出和附件必须消费字段投影，不得只消费 ID 集合。

### D-05：Provider secret 只允许一条真实路径

数据库 Provider 配置是产品管理面；ProviderGateway 是执行面；sidecar 环境变量不能成为第二套生产配置。开发 stub 必须显式标记，并在生产启动拒绝。

### D-06：Agent 是系统能力，不是系统管理员的越权身份

Steward 权限来自 `space_id + job_id`，Assistant 权限来自 `account_id + session_id + space_id`。平台 operator 负责代码、Provider、工具白名单和安全策略，但不因该角色获得家庭数据读取能力。

### D-07：事件是可重放产品事实

状态变化没有公开终态事件就无法可靠恢复。reaper、cancel、settle 和 sidecar 必须复用事件持久化/广播边界；SSE 关闭连接不是事件的替代品。

### D-08：删除传播使用“先失效、后清理”

授权谓词、RAG 查询、DerivedFact 读取、导出 grant 和附件下载先看到 revoked/tombstone；物理 FTS/embedding/文件清理是后续优化，失败不会让旧数据重新可见。

### D-09：不使用 Luna 子代理

本任务工件由主线程创建和复核，未启动 Luna 子代理；后续实现按当前运行环境和用户允许的模型选择，不把某个不可用模型写成任务依赖。

## 2. 风险登记

| ID | 风险 | 等级 | 处理 | 关闭条件 |
|---|---|---:|---|---|
| R-01 | 归档 JSONL 路径失效，后续 Agent 无法加载研究 | P0 | archive-aware resolver + 全量 validate | active/archive 全通过 |
| R-02 | 建档关系拆分造成孤儿 provisional 档案 | P1 | 原子 command + rollback/idempotency 测试 | 中途失败无残留 |
| R-03 | stats/custody 绕过字段投影 | P1 | 统一 VisibilityDecision 和字段 DTO | 全出口字段矩阵 E3 |
| R-04 | 导出明文或崩溃停留 processing | P1 | envelope encryption + grant/reaper | 静态无明文 + crash 恢复 |
| R-05 | Provider 管理配置无法驱动 sidecar | P1 | ProviderGateway 单一路径 | DB config Compose E2E |
| R-06 | internal API 宿主可达 | P1 | private listener/network + token | nginx/host negative E3 |
| R-07 | schema 漂移允许非法工具输入 | P1 | 单一合同/递归校验/快照 | 三方合同测试全绿 |
| R-08 | SSE 终态缺失、user event 重复 | P2 | 单点事件生产 + terminal event | replay/unique tests |
| R-09 | audit actor/错误日志脱敏不一致 | P2 | typed actor + centralized redact | log/event scan zero |
| R-10 | 375px 与跨空间证据不足 | P2 | 人工走查 + synthetic E2E | 记录截图/命令/退出码 |

## 3. 不改变的决策

- 不做真实生产数据迁移、双写、回填或旧客户端兼容窗口。
- 不物理合并 HouseholdSpace/LineageSpace，不自动发加入申请，不自动扩大公开范围。
- 不让 LLM 计算亲属路径、判断最终权限或写 SourceFact。
- 不把多人使用称谓提升为全局模板；保留个人/空间/地区/系统四级优先级。
- 不把聊天全文自动写入 RAG，不采集键鼠/停留时长，不让 Steward 读取私人 Session/Memory。
- 受控 Web 默认关闭，Steward 永不获得 Web 工具。

## 4. Deferred / 非阻塞技术选择

- Provider 具体品牌、加密库/密钥托管、internal listener 的进程拆分、首批地区语言包和 embedding 适配器可在实现阶段选择。
- 这些选择不能改变：无静默 fallback、secret 不落日志、local-required 不发云、scope/visibility 先过滤、Web 默认关闭和空库可复现。

## 5. 证据记录模板

整改实施后每条 AC 追加以下结构，不用口头描述替代：

```yaml
ac: AC-...
status: passed | partial | blocked
commit: <sha>
command: <完整命令>
exit_code: 0
tests: <数量与关键断言>
artifact: <日志/截图/报告/CI URL>
notes: <环境限制、残余风险、复核人>
```

任何 `partial` 或 `blocked` 都必须保留在 handoff 和 task metadata 中，不能因为任务目录要归档而删除。
