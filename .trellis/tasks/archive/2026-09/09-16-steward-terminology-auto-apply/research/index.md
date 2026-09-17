# 称谓自主优化研究路由

- 已确认：父母展开路径缺少旁系词条查找；四条配偶旁系词方向错误；derived baseline 会生成可选保留建议；通知中心将其放入待核实。
- 已否决：投影非空才代表自动改善；现有 suppression helper 能直接用于所有位置折叠；历史 535 条观测能代表当前生产状态。
- 决策：确定性 baseline 直接改善、模型合格结果自动投影、可选偏好不占待办，保留显式词条和事实/授权边界。
- 阻塞产品问题：无。
- [结论与实施约束](terminology-summary.md)
- [本轮源码与纯函数证据](evidence/terminology-investigation.md)
- [真实模型验收记录（AC6/AC7/AC9）](model-acceptance-2026-09-17.md)：隔离库真实 Provider 全链已通过，AC7 因“无可改善目标”阻塞。
