# PRD：RAG 索引补建与生命周期治理

## Goal

让合法历史记忆在 RAG 有效开启后自动补齐索引，同时确保重试、重建和换版不会复活撤销内容或改变既有引用含义。

父任务：[治理总任务](../09-13-agent-memory-rag-remediation/prd.md)。D 负责 MR-13、MR-25、MR-15 的生效状态，以及 MR-17 的检索失效边界。任务默认 P2，但 MR-13/MR-25 按 P1 验收；实施顺序等待 A/B 合同稳定，不表示可忽略生命周期缺陷。本轮仅规划。

## Background

关闭 RAG 时确认的记忆不会建索引；开启只更新开关，没有生产补建入口。隔离实测开启后命中 0、显式 rebuild 后命中 1。

规划复核又发现：rebuild_index 只找 active document，找不到即调用 index_memory；后者能把同 revision 的旧文档设回 active 并删除重建 chunks。此为静态通路，尚未复现线上事故。把 helper 直接接到定时维护可能放大风险，因此必须先修状态和幂等。

当前 maintenance 启动条件主要覆盖 AGENT_RUNTIME/STEWARD，需要纳入 RAG-only 和部署允许但平台稍后开启的情况。当前生效语义是平台 DB 开关与部署 hard-off 共同约束，本任务不改变它。

## Requirements

- D-R1：同一来源内容版本只有一个规范 document；重试不重复 Memory/document/chunk，不改变同切分版本的引用定位。并发执行由数据库约束及事务裁决，不能只依赖进程内锁。
- D-R2：区分从未建索引、处理中、当前有效、索引算法换版、来源已失效。来源 tombstone、撤销、删除、到期、无权依赖、legacy/unverified 不得被重建重新索引。
- D-R3：RAG 关闭期间形成的合法已确认记忆，在有效开启后进入有界补建；重启、中断、失败重试、多执行者不漏项或无穷重试。流程独立于 Steward 与辅助模型开关。
- D-R4：开关写接口保持小事务；不在 admin PUT 或用户读请求中同步扫描全库。运行时关闭后不再启动新批次；已取得任务在提交前重验状态。
- D-R5：物理 FTS 重建与来源物化分离，只重建当前合法投影；B 的 index_version 换版可观测、可恢复、可回滚，不把索引升级当作内容重新确认。
- D-R6：只能输出安全计数、进度、版本、错误码和耗时；家庭用户只看有权范围，管理员治理面不暴露家庭原文/摘要。明确处理中与失败，不能把“RAG 开关打开”展示成“资料全部可检索”。
- D-R7：存量重复、未知 tombstone 原因和不可验证来源先报告/隔离，保留原文与确认历史；未经确认不做破坏性去重或强制迁移到授权状态。

## Acceptance Criteria

| ID | 可观察结果 |
|---|---|
| D-AC1 | 隔离库先关闭保存、后开启，由真实维护入口有限轮次补齐合法记忆，无需再次确认；记录批量大小/完成条件 |
| D-AC2 | 反复直接 index/rebuild、维护重试和两个执行者竞争均不增加同源同版本重复行，同版本 chunk ID 与引用稳定 |
| D-AC3 | 同 revision tombstone、已删/撤销/到期 Memory、RAG 依赖失效和 legacy/unverified 均不复活；原始正文/确认历史未被清空 |
| D-AC4 | 在扫描/提交之间撤销来源、移除成员资格或关闭 RAG，提交及后续读取保持有效状态边界；某一读者失权不全局删除他人可读来源 |
| D-AC5 | 批次中断、失败退避、重启、RAG-only 部署及平台晚开启按有界游标恢复；失败登记/游标原子提交，过期执行者不能回退游标/活动版本 |
| D-AC6 | 新检索只读活动切分版本；旧引用/保存依赖按原片段精确授权读取，换版不改变含义；FTS 重建不修改来源状态、confirmation 或 tombstone |
| D-AC7 | 四种 Memory/RAG 组合及部署 hard-off/平台开关组合的有效状态正确；维护不调用 Steward/模型、不持有全库长事务 |
| D-AC8 | 真实迁移库包含旧数据/重复/失效样本，迁移报告可审阅；安全治理元数据无正文/密钥，相关检查通过 |

## Dependencies and boundary

等待 A 的 source resolver、legacy/unverified 与来源依赖模型；等待 B 的 document/chunk/index_version 合同。C 不是 D 的数据依赖，默认父计划 A→C→B→D 串行。

Steward 辅助平台开关的 admin API/UI/审计属于 [既有开关任务](../09-13-steward-assist-platform-switch-admin/prd.md)，不得在 D 复制页面/理由计算。D 只提供 RAG 索引状态和必要维护职责；若需要共同管理页面，由唯一所有者串行集成。

不做物理擦除、不上传外部向量库、不接文档导入或 Steward RAG。新增迁移编号在实施当日确认，不预留固定号码。验收详见 [验证计划](../09-13-agent-memory-rag-remediation/research/validation-plan.md)。
