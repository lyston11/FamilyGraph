
## 2026-09-12 研究轮

已基于当前代码和已完成发布证据完成研究：`research.md`。结论是共享知识辅助/个人路径解释可进入独立 MVP 设计评审，但默认关闭且不得覆盖确定性 SourceFact/PFV；地区能力只允许有来源词包，当前 wu 包不足以宣称覆盖；性能重构暂不实施，等待生产指标触发。

已核对 `memory_rag.search_rag` 的 Steward policy consumer、space/confirmation/status/author-visible 过滤，`TermRegistry` 的四级优先级和现有 locale 覆盖，以及发布轮确认的 PFV 全矩阵成本。当前未修改产品代码，未创建实现子任务；仍保持 planning/deferred_design_review。

## 2026-09-13 称谓职责交接

用户明确要求补齐个人称谓显示、自动建议与模型优化，生产所有权转交 09-13-steward-kinship-capability-closure 及其 A/B 子任务。本任务剩余 shared RAG、额外解释/地区包和条件性能研究继续延期；旧“尚未创建实现子任务/模型能力延期”只描述当时研究，不覆盖此次独立授权。任务状态与既有研究结果保持不变。


## 2026-10-06 验收与归档

**交付物已按 AC-1..AC-4 逐项核对并补齐缺口**：

| AC | 状态 | 证据 |
|---|---|---|
| AC-1 | 满足 | `research.md` 合成问答矩阵 6 对（≥6），含权限/确认/撤销/冲突优先 |
| AC-2 | 满足 | `research.md` 个人路径解释准入：缓存键含 `account_id/root_user_id/space_id/view_version/input_hash/policy_version`，两个 viewer 不得共享 projection |
| AC-3 | **本轮补齐** | `research/locale-coverage.md`：现场统计 system 25 / zh-CN 112 / wu 1（共 138），按编码类别与段深分解；**未知码降级已实测**（合法但未覆盖 → `source_level='derived'` 结构化描述，不伪造称谓；格式非法 → 422） |
| AC-4 | 满足 | `research.md` 性能触发条件 + 保序/幂等/CAS/权限等价要求；当前无超限证据故 NO-GO |

**决策结论：保持 deferred**。四个能力的 go/no-go 与前置条件已登记；共享 RAG 与个人解释
需用户明确「首批文档管理者、展示位置、地区列表、启用开关」后才可拆实施子任务。

**为什么不实施**：这不是「没做完」，而是 AC 要求的决策结果——没有真实资料样本时启用
RAG 无法证明增益（AC-1 的 A/B 要求），地区包只有 wu 单条演示不足以宣称覆盖（AC-3）。

**保护信号**：跨空间/称谓相关的安全边界已在项目记忆中（`#298`、`#307`、`#330`），
不依赖本任务保持 active 来阻止误实现。
