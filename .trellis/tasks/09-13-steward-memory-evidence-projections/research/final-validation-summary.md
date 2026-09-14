# 最终验收摘要

SP-AC1～6 全部通过。MR-26 保留重建器不拥有的冷却与未知键；MR-23 将相关支撑记录为不可变内部版本，保留稳定候选、确认和驳回历史。新证据不新增建议、收件人、通知或推测边，反向 sibling 候选同样隔离。

实施审查另复现并修复了 assist 等待 SQLite writer 后使用旧时间放行过期租约的问题。后续 core 捕获精确版本 ID；两个 pending 版本独立交付，核验与回执同事务回滚/恢复。0049 原地升级保留历史，有版本或已采用归因状态时在首个 DDL 前拒绝降级，并复用祖先拒绝条件。

- 定向回归：188 passed，84.19 秒。
- 完整后端：1594 passed / 3 既有 skipped / 4 既有 warnings，156.36 秒。
- 六个既有测试 import 整理后：43 passed，7.42 秒；生产文件与三套新测试未变。
- Ruff、format（394 文件）、mypy（205 源文件）及独立最终 `trellis-check` 通过，无遗留可操作缺陷。

限制继续有效：仅认证共同 biological_parent 支撑的 direct_sibling；projected 是历史核验，不是持续有效的正式亲属事实。原 candidate→首次 job CASCADE 保留。测试使用合成迁移库和 fake transport；未操作生产数据、模型、开关或部署。本任务仅改后端，前端构建、浏览器、生产 smoke 和容量性能实测未重复执行。

需要核对具体命令、AC 到测试的映射或早期失败时，读取 [验证记录](validation.md)；源码与日志 hash 见 [最终机器证据](evidence/final-validation.json) 和 [源码清单](evidence/backend-source-manifest.json)。写锁旧时间的原因与原红测见 [调查](evidence/lease-writeback-investigation.md)。
