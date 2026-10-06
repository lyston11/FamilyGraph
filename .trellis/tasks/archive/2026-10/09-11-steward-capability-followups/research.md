# Research — Steward 后续能力准入评估（2026-09-12）

## 结论摘要

**共享知识辅助：有条件可做；个人路径解释：可做但应先确定性模板；地区称谓：小规模词包可做，不能由模型自由生成；性能优化：暂不实施，先保留观测触发器。**

## 代码证据

- `memory_rag.search_rag` 已接受 `agent_kind`，先做 SQL scope/status/confirmation 过滤，再做作者可见性复核；`steward` 不是 assistant，因此 private 与 public 分支不会被误读，household/lineage 必须绑定当前 `space_id` 和 active membership。
- `context_builder` 已有共享 ContextBuild/版本化投影，但新能力必须绑定 `viewer/root/space` 与当前 PFV input version，不能把一个用户的解释缓存给另一个用户。
- `TermRegistry` 已有 `personal > space > locale > system`，zh-CN 覆盖 `Dm-Sf→儿媳`、`Df-Sm→女婿` 等黄金码，wu 只有 `Um→阿爷` 演示包；因此地区扩展的真实缺口是词表和来源，不是再造算法。
- 200 人全矩阵重算已被 release evidence 记录为小时级，并超过默认 lease；但没有受影响子图算法的正确性/公平性证据，不能直接引入并行图服务。

## 共享知识辅助准入

必须同时满足：有至少一批 confirmed/authorized shared 文档样本；每条回答能引用 `rag source/revision/chunk`；membership、disclosure、tombstone 后重验失败；对同一结构关系做结构事实与知识解释的 A/B 增益评估。未满足时保持结构化模板，不启用 RAG。

合成问答矩阵（预期）：

1. 当前空间已授权族谱资料 + `Dm-Sf`：返回“儿媳”及来源，结构称谓优先。
2. 同空间 confirmed 家谱说明 + 隐藏作者文档：不返回隐藏文档。
3. private memory + Steward consumer：零命中。
4. household 文档在另一空间：零命中。
5. revoked/tombstoned 文档：零命中或 evidence invalidated，不保留旧引用。
6. shared 文档与 SourceFact 冲突：SourceFact/确定性 resolver 胜出，知识只能作为冲突提示。

## 个人路径解释准入

第一阶段只输出确定性模板和已授权 path evidence；模型最多润色解释候选，不产生关系事实、称谓、权限或新的路径。缓存键必须包含 `account_id, root_user_id, space_id, view_version, input_hash, policy_version`。提交/展示前重新读取 PFV 并做 evidence fence；撤权、bridge 过期或路径失效立即丢弃候选。

若两个 viewer 的路径、披露或称谓偏好不同，必须生成两个不同 projection/candidate；不能按 space 共享解释结果。

## 地区称谓准入

当前证据只有 zh-CN 完整黄金包与 wu 单条演示，不能宣称方言覆盖。新增 locale 需要：来源可追溯词表、至少一组 parent/child/spouse/in-law/generation 测试、同义词选择规则、未知码 fallback、个人/空间纠正优先级回归，以及未成年人/敏感称谓的内容审核。模型生成词不得直接写入 system/locale；只能进入个人候选并经用户确认。

## 性能触发器

仅当真实生产指标显示 PFV backlog 持续超过扫描窗口、单租约计算超过 TTL、或增量结果与全量结果差异可控时，才进入子图重算/独立 worker 设计。任何优化必须证明保序、幂等、CAS、防旧结果覆盖、授权等价，并保留全量 rebuild 作为审计基线。当前不引入 Redis、图数据库或第二索引。

## Go/No-Go

本研究建议：共享知识和个人解释 **GO 进入独立 MVP 设计评审，但默认关闭**；地区称谓 **GO 仅做有来源词包**；性能重构 **NO-GO（继续观测）**。需要用户后续明确首批文档管理者、展示位置、地区列表和启用开关，才可创建实施子任务。
