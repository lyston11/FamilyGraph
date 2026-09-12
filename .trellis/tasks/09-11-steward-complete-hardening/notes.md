# 决策、纠正与交接记录

## 2026-09-11 用户授权

用户接受本会话 grilling 推荐：确定性 Steward 正式定义、模型可选、先可靠性再候选审核、用户确认而非模型改事实、站内通知；并要求所有缺陷/漏洞/后续事项形成对应 Trellis 任务及详细 PRD、implements、notes。
本仓标准文件名为 implement.md，另有 implement.jsonl；不重复建立 implements.md 造成两份计划漂移。

## 对前轮结论的纠正

1. Steward 已存在，且 09-06 起已能在开关下调用模型；“当前完全不调用 LLM”不准确。缺独立 Pi 会话不是缺陷。
2. 已有 ActionCard 站内通知及认领后 within-scope 推荐；新任务只补差额，不重建。
3. 46+16 tests 是 09-09 当前会话报告，09-11 本轮没有重跑，不能作为新改动或全链路上线证明。
4. 确认事实不能只写成 owner 点一下。commands/connections 和 SourceFact FSM 有不同职责，必须尊重当事人确认、代管权限及独立授权命令。
5. 长事务内模型 HTTP、预算仅计 succeeded、失效前缀不一致、PFV term 用结构说明、policy='graph' 等实际缺陷已进入独立主责任务。
6. 跨空间匹配不是永久删除需求：用户选择后置，现建立 deferred design 任务。Pi 人格/越权行为仍明确不做。

## 明确不做

- 独立 Pi Steward 聊天人格、复活 generic AgentJob(kind=steward)、任意 MCP/shell/自主工具规划。
- 模型决定权限/正式关系/成员资格，自动发申请、自动合并空间、读取私人会话记忆。
- 强制把每个只读推荐变成 ActionCard，或新建第二个 Notification/关系算法/词典体系。
- 为了“完整”直接部署 Redis/图数据库/独立进程；由容量证据触发性能研究。

## 后续而非当前实现

capability-followups 负责共享知识、个人路径解释、地区内容和条件性能研究。
cross-space-discovery 负责未来 opt-in、最小披露、威胁模型与 go/no-go；当前不能运行真实匹配。
短信/邮件/外部推送本轮明确不做，现有站内通知足够；后续只有明确新产品需求才建实施任务。

## 工作区与审查

已有空间关联、前端、conftest 等未提交变更保持原状；本轮新增/更新 .trellis/tasks/09-11-steward-* 文档，不修改产品源码、.env、运行服务或提交 git。
探查子代理本轮均因上游 503 失败，主线程接手定点核查，没有把空结果视为无问题。
JSONL 已改为 spec/research 文档，避免先前注入大代码/长架构被截断；权威架构仍需执行者自己读相关完整章节。

## 完成口径

当前完成的是规划资料与可追溯任务树。产品缺陷未修复，真实 provider E2E 未新增。结构校验报告由本轮工具运行结果确认，不以 task.py validate（只验证 context）代替内容审查。

本轮 [规划工件校验记录](research/planning-validation.md) 已记录九个任务的原生校验、需求/验收映射、依赖及引用检查结果。逐条关闭事项时使用 [事项—任务—验收追踪矩阵](research/coverage.md)。

## 2026-09-12 执行轮完成记录

用户授权"完全执行直到完成"。本轮按子任务顺序执行：production-ops → assist-execution →
projection-consistency → quality-security → candidate-review → release-observability。
前五个子任务由 trellis-implement / trellis-check 子代理实现并核验（各 PASS）；后两个因
上游模型请求中断改由主会话直接完成（用户明确指示后续不再使用子代理）。

- 迁移链：0035（既有并行空间 lineage 工作，先于本轮）→ 0036 调度 → 0037 辅助批次 → 0038 建议。
- 全量门禁：backend pytest 956 passed / 3 skipped；frontend 525 passed；system-admin-frontend 83 passed；
  ruff/mypy 本轮文件 0 问题；迁移往返、E2E、容量采样脚本退出码 0（临时 DATA_DIR）。
- E2E 发现并修复真实缺陷：submit 路由 datetime 序列化 500（补路由级回归）；
  test_config 阈值用例的 config 模块属性泄漏（影响全量稳定性）。
- 诚实口径：真实 provider E2E 未运行，模型行为证据均为受控 fake transport 程序合同；
  发布门禁 partial（见 release-observability/release-evidence.md 缺口清单）。
- F22/F23（capability-followups / cross-space-discovery）为延期研究，归档父任务时解除
  parent 关联保留活动，不随父任务假归档。
- 本轮提交包含工作区中先于本轮存在的 0035 空间 lineage 未提交前置工作（0036-0038 的
  down_revision 依赖它，无法拆分提交）。
