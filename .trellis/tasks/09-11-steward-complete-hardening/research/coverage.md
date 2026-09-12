# 事项—任务—验收追踪矩阵

主责任务唯一；其他子任务消费共享合同或集成验证。验收编号对应主责任务 PRD，实际结果逐条记录 notes。

> 状态（2026-09-12 执行轮结束）：F01–F21 已闭环，逐条证据见
> [findings.md 修复状态回填](findings.md#修复状态回填20260912执行轮结束) 与各子任务 notes.md；
> F22/F23 为延期研究，保持活动不随父任务归档。真实 provider E2E 全轮未运行，
> 模型行为证据均为 fake transport 程序合同，发布门禁 partial。

| 事项 | 主责任务 | 直接 AC 出口 | 联合验证/后续 | 状态 |
|---|---|---|---|---|
| [F01](findings.md#f01) | [09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md) | AC-1 | release-observability AC-2/AC-5：有效配置与重开验证 | 已闭环 |
| [F02](findings.md#f02) | [09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md) | AC-3, AC-4 | release-observability AC-1/AC-5：进程恢复 | 已闭环 |
| [F03](findings.md#f03) | [09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md) | AC-2, AC-5 | release-observability AC-1：自动扫描与显式重跑 | 已闭环 |
| [F04](findings.md#f04) | [09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md) | AC-1, AC-5 | production-ops AC-4：多执行者仍可推进 | 已闭环 |
| [F05](findings.md#f05) | [09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md) | AC-2, AC-3 | release-observability AC-5：恢复证据 | 已闭环 |
| [F06](findings.md#f06) | [09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md) | AC-3 | quality-security AC-5：预算耗尽安全降级 | 已闭环 |
| [F07](findings.md#f07) | [09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md) | AC-1, AC-2, AC-3, AC-4 | quality-security AC-2/AC-3：闭合类型与证据约束 | 已闭环 |
| [F08](findings.md#f08) | [09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md) | AC-2, AC-5 | candidate-review AC-1/AC-5：安全呈现 | 已闭环 |
| [F09](findings.md#f09) | [09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md) | AC-1, AC-3, AC-5 | candidate-review AC-4：证据变化使建议失效 | 已闭环 |
| [F10](findings.md#f10) | [09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md) | AC-3, AC-5 | quality-security AC-2：隐藏人物不得输出 | 已闭环 |
| [F11](findings.md#f11) | [09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md) | AC-4 | capability-followups AC-3：地区词表内容另行研究 | 已闭环 |
| [F12](findings.md#f12) | [09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md) | AC-5 | quality-security AC-2：陈旧解释回退 | 已闭环 |
| [F13](findings.md#f13) | [09-11-steward-projection-consistency](../../09-11-steward-projection-consistency/prd.md) | AC-2, AC-5 | release-observability AC-1：真实注册/认领 API | 已闭环 |
| [F14](findings.md#f14) | [09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md) | AC-5 | release-observability AC-1：通知到用户操作 | 已闭环 |
| [F15](findings.md#f15) | [09-11-steward-candidate-review](../../09-11-steward-candidate-review/prd.md) | AC-1, AC-3, AC-4, AC-5 | release-observability AC-1：发现到处置收敛 | 已闭环 |
| [F16](findings.md#f16) | [09-11-steward-release-observability](../../09-11-steward-release-observability/prd.md) | AC-3 | assist-execution AC-2/AC-4：辅助审计安全错误码 | 已闭环 |
| [F17](findings.md#f17) | [09-11-steward-production-ops](../../09-11-steward-production-ops/prd.md) | AC-2, AC-4, AC-5 | assist-execution AC-4：辅助写回版本栅栏 | 已闭环 |
| [F18](findings.md#f18) | [09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md) | AC-1, AC-5 | assist-execution AC-4：出站与写回均重验 | 已闭环 |
| [F19](findings.md#f19) | [09-11-steward-assist-execution](../../09-11-steward-assist-execution/prd.md) | AC-5 | quality-security AC-2/AC-3：内容和数量语义约束 | 已闭环 |
| [F20](findings.md#f20) | [09-11-steward-quality-security](../../09-11-steward-quality-security/prd.md) | AC-4, AC-5 | release-observability AC-1/AC-5：真实业务与 provider 证据 | 已闭环（stub 口径；真实 provider 证据未取得） |
| [F21](findings.md#f21) | [09-11-steward-release-observability](../../09-11-steward-release-observability/prd.md) | AC-1, AC-5 | 父任务 AC-P5：更新现行合同，保留旧历史 | 已闭环 |
| [F22](findings.md#f22) | [09-11-steward-capability-followups](../../09-11-steward-capability-followups/prd.md) | AC-1, AC-2, AC-3, AC-4 | 延期研究；未达价值/容量门槛保持不实施 | 延期（活动） |
| [F23](findings.md#f23) | [09-11-steward-cross-space-discovery](../../09-11-steward-cross-space-discovery/prd.md) | AC-1, AC-2, AC-3, AC-4 | 延期研究；新的 go/no-go 决策前不进入实现 | 延期（活动） |
