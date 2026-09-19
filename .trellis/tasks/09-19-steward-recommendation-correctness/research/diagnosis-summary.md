# 诊断核对摘要

## 结论与可信边界

2026-09-19 用户要求为已报告的两类管家误推荐建立修复规划。本轮重新核对本地主检出源码与当前合同，没有重新读取线上数据库，也没有执行修复或生产写入。

前次对话中的“26 条全部无效”“21 张卡全部无效/分布于 21 个 lineage 空间”不能从给出的统计可靠推出，且前次分类相互矛盾。任务不沿用这些数量作为清理条件；正式执行时按统一判据逐对象输出 dry-run。

## 已核对调用链

| 路径 | 当前问题/约束 |
| --- | --- |
| `backend/app/services/steward_guard.py::validate_candidate_output` | 结构/kind/代号/端点/年龄校验不等于已有关系冲突校验 |
| `backend/app/services/steward_candidate_evidence.py` | 共同生物父母证书仅支持 direct_sibling；legacy/unsupported 可保留旧公开流程；versioned 双向粘性隔离，不因支撑丢失再公开 |
| `backend/app/services/steward_suggestions.py::project_for_job` / `source_state` / `effective_state` / `submit_suggestion` | 公开投影、个人有效状态、提交需要同一负向判据；同类型 find_confirmed_relation 去重不能识别亲子/祖先与 sibling 的冲突 |
| `backend/app/services/steward_inferred.py::_edge_from_candidate` / `active_edges` / `confirm_edge` / `reinstate_edge` | 独立推测通道必须同时接线，不能只藏通知仍允许推测转正 |
| `backend/app/services/steward.py::_pair_inputs` | 当前仅 space.kind=household 才算双方是否同为本空间 active；lineage 输入 shared household 恒 false |
| `backend/app/services/recommendation_matrix.py` | spouse 分支基于该 bool 产生 create_household；矩阵纯函数应保留。creation_choices 来自 profile ref，表空本身不证明 bug |
| `backend/app/services/steward_delivery.py::prepare_intents` / `_apply` / `_card_review` | staged 主链每代准备卡复核、事实推荐、推测边与候选意图；实际交付须重验，旧同步路径修复不足以修复线上主链 |
| `backend/app/api/action_cards.py::execute_card` / `_create_shared_household` | 卡执行重验后调用 Foundation 命令；应在管家卡边界抑制重复，不全局禁止自主创建多个家庭 |
| `backend/app/services/source_facts.py::_ancestors_within` | 既有父系遍历没有本任务要求的当前空间限定且混合 parent 类型，不能直接用作本次 biological ancestry 判据 |

## 必须保留的产品决定

- `.trellis/HANDOFF.md` 的现行合同：个人呈现不创造事实；普通 GET 不调用模型/创建事实；私有会话与 Memory 不进入 Steward 关系上下文；跨空间发现仍延期。
- `.trellis/tasks/archive/2026-09/09-13-steward-memory-evidence-projections/{prd,design,implement}.md`：共同父母证书只内部保存、核验，不新增关系通知、recipient、待核实建议或推测边，保留驳回与首次归因。
- `.trellis/spec/backend/steward-candidate-evidence.md`：unsupported 不等于冲突，versioned 不等于事实确认，证书不能被伪造为清理工具。
- `.trellis/spec/backend/steward-action-card.md`：保留 staged snapshot/publication/lease、内部执行身份、冷却、状态机与命令事务；大文件实施前需分段读全文。
- `.trellis/spec/architecture/0--0-v2-2026-08-26-v1.md`：共同空间显式建立，不自动合并；四级可见性/purpose 不扩大授权。

## 修订提案与决定

- 候选新增独立负向安全判据，不扩展正向证书，不把所有不同关系设为互斥，不重构称谓呈现。
- confirmed 直接生物/收养/继亲 parent 对采用 sibling 推荐抑制；祖先路径仅沿 biological_parent，不把混合继养监护路径折为血亲。guardian/spouse 不单独构成排斥条件。
- 共同 household 只做负向布尔查询，不将其他空间 ID/名称/名单提供给 API、模型或日志。这是对旧本空间判据的有限修订，待用户审阅本规划批准后实施。
- 用户历史保全与实际消失分别验证：effective_state 使旧线索退出活跃消费，后台既有 review/FSM 退役派生边/卡，不伪造已解决，不自动撤回 SourceFact。
- 当前空间输入 revision 不能代表他空间成员变更；因此读和命令即时 live 重验，后台下一次健康扫描/交付收敛。不能只依赖证据 hash 或误承诺无队列延迟。

精确方案及验收矩阵见任务 `design.md` / `prd.md`，操作顺序和回退点见 `implement.md`。这些是待执行计划，不是验证通过的证据。
