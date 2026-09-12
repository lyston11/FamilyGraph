# 当前合同与规划约束（2026-09-11）

## 已存在且必须复用

- `backend/app/services/domain_events.py:66 emit` 是事件入口；同事务登记 scope job。
- `backend/app/services/steward.py:639 run_steward_job` 及 `maintenance.py:43 run_maintenance_tick` 已有真实生产 core 执行器。无需重新发明引擎、用 Pi 替换或建第二个 generic queue。
- `backend/app/commands/identity.py:29 claim_and_confirm_own_identity` 和 `commands/members.py:790` 都发 account.claimed；这些不是新的待修缺陷。
- `backend/app/services/family_recommendations.py:49 recommendations_payload` 和 `/api/family-recommendations` 已有 current PFV 内的亲属推荐，需守住不产生 membership/source fact 的只读语义。
- `backend/app/services/notifications.py:88 record_action_card_notification`、`action_cards.py:305` 已把出卡变为站内通知；现有 Notification + read/read-all + NoticeItemRow 为唯一通知基础。
- `backend/app/services/terms.py:164 resolve_term` / `:738 resolve_term_or_structural` 和 `:267 set_personal_term` 是词典口径；RawRelationInput 原文不可变。
- `backend/app/services/memory_rag.py:620 search_rag` 已有受限 consumer；不能因为缺共享知识辅助产品面就删除其 Steward 身份。

## 关系提案不是权限

`commands/connections.py:49 create_connection_request(session, ctx, *, target_id, dir_class, label, space_membership_space_id=None)` 发 pending 请求；`:119 decide_connection_request(session, ctx, edge_id, *, accept)` 经实际被请求方/关系 FSM 后才映射 confirmed SourceFact。
该机制不支持任意两位第三方由 owner 代替确认；candidate-review 不得伪造 ctx。elder/younger 结构方向不等于所有原子关系类型，不能将收养/继亲强行映射为生物学亲子。

`services/source_facts.py:195 create_source_fact(..., state='proposed')` 与 `:259 transition_source_fact` 是领域服务，后者验证 FSM 不验证 actor 授权；新增 API 必须有命令层授权/当事人确认/环检查/证据版本。
当前 SOURCE_FACT_TYPES 为 biological_parent/adoptive_parent/step_parent/guardian/spouse/partner/direct_sibling；provenance 已有 agent_proposal，可记录来源但不能由此提升权威。

## 架构不变量

- 系统 admin 是独立主体/listener；元数据诊断、重跑不授予家庭内容权。家庭用户负责自己有权的提案/资料确认。
- 产品 Agent 两种，通用 Pi 会话 runtime 仅 Assistant；Steward core + auxiliary batch 归 canonical StewardJob。
- SourceFact/关系/成员/bridge 状态各自真源；候选、通知、PFV、模型解释只是投影。
- LLM 不决定授权、路径算法、事实状态、成员资格。模型候选可以是待验证线索，但不能混为确认亲属/树节点。
- API 是唯一外部模型出口，密钥在服务端解密，不落 prompt/log/audit；space cloud consent 和 local_required 都要检查。
- 核心写事务短且没有网络；发包与应用前检查权限/输入版本，后台处理不能绕开 VisibilityPolicy。

## 规范加载

现行 `.trellis/spec/architecture.md` 很长且含 v1 被覆盖内容，需主执行者按相关章节完整读取，不能在 JSONL 注入被截断的整份架构后假定已读全。本摘要用于定位，不能取代该权威文件。
JSONL 只挂真实 spec/research 文档；不注入巨大代码或测试文件。代码位置记录在 findings 与每个 implement.md 中，实施者自己读。

## 工作区与迁移

已有未提交 backend/app/commands/spaces.py、models/space.py、前端与 conftest 等改动，另有 0035_space_lineage_link.py；本次任务不能覆盖它们。开始实现时以最新 schema head 分配 migration，声明与并行空间工作的边界。
当前 conftest 已在导入 app 前强制 tempfile.mkdtemp 作为 DATA_DIR，再建/回滚测试数据库；保持该隔离。独立迁移和 E2E 也必须指定临时 DATA_DIR。真实业务库不能用作测试/迁移往返样本。
