# Implement — Steward 跨空间亲属发现：延期设计与准入条件

## 开始条件

- [ ] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [ ] 确认依赖：`09-11-steward-release-observability`。
- [ ] 取得最新实施摘要的明确批准再 task.py start；当前仅规划。

## 有序执行

- [ ] 1. 读取已归档推荐/bridge 决策和本轮修复验收结果，记录与现有产品的差别。
- [ ] 2. 用合成两个家庭构造威胁场景，不查询真实家庭数据。
- [ ] 3. 列出最小披露、双边同意、令牌生命周期与错误矩阵，标注不可证明的假设。
- [ ] 4. 提交价值/安全 go-no-go 材料；在新的范围批准前不编写生产匹配代码。

## 改动边界与重用位置

- `backend/app/services/family_recommendations.py`
- `backend/app/services/personal_family_bridge.py`
- `backend/app/services/visibility.py`

## 验证计划

本任务研究阶段只核验文档/证据/场景，不跑生产匹配或外发请求。将来实现另立验证任务。

## 回滚与交接

- [ ] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [ ] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [ ] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
