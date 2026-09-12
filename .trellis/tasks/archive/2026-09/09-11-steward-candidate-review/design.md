# Design — Steward 候选审核、冲突待办与站内通知闭环

> 本文是待实施技术方案，不是当前代码行为；现状证据见父任务 findings。

## 建议模型与生命周期

新增受控 `StewardSuggestion` 作为可审核投影，现有 `steward_llm_candidates` 是内部生成记录，保留 source_candidate_id 关联。字段：space_id、origin(deterministic/model)、kind、subject/object、evidence_json（只存允许 ID/revision）、evidence_hash、policy_version、status、revision、expires_at、superseded_by_id、关联 source_fact/relation/term id。收件表 `StewardSuggestionRecipient(suggestion_id, account_id, dismissed_at, read_at, cooldown_until)` 约束唯一。

种类首版：relation_proposal、term_preference、identity_duplicate、missing_information。后两类来源为现有 `_emit_new_findings`，只指向人工处理，不引入通用任意动作。去重键包含 space/kind/有向端点/结构化建议值/evidence_hash，不包含模型措辞；term_preference 另含 viewer account。并发生成由唯一约束收敛，驳回冷却只作用于同一收件人的同一证据版本。
建议状态：关系建议 proposed → submitted → resolved；个人词偏好或资料问题可在对应领域变更后 proposed → resolved。proposed 可 expired、superseded；dismissed 是收件人独立状态，不因某人驳回让整个建议终结。submitted 的终局由关联领域对象决定。拒绝一个建议不撤销已经存在的正式事实。

## API（拟定）

- `GET /api/steward-suggestions?space_id=&cursor=&limit=`：默认 20，最大 100，返回 items/next_cursor。item 只含安全显示、kind/state/revision、证据摘要、allowed_actions；不返回 raw model payload。
- `POST /api/steward-suggestions/{id}/dismiss`：expected_revision，幂等、按收件人冷却。
- `POST /api/steward-suggestions/{id}/submit`：expected_revision、evidence_hash、明确 confirm=true、Idempotency-Key；动作/对象完全由服务端建议决定。relation_proposal 返回 202 `{suggestion, linked_proposal, pending_confirmations}`，绝不把 submitted 显示为关系已确认。term_preference 仅由本人显式提交，调用现有个人词偏好命令后返回 200 `{suggestion, linked_preference}` 并 resolved；不创建关系提案或修改空间/系统词典。identity_duplicate/missing_information 首版仅提供 open_details/dismiss，没有 submit；由有权用户在既有资料流程操作，后续 core 检测条件消失才 resolved。
- 仅关系提案尚无等价命令时，增加受控 `commands/relationship_proposals.py`：两端主体的授权确认记录、expected fact revision、环检查与 SOURCE_FACT_TYPES 白名单；有权 owner 可以发起不能代确认。代管身份沿用现有 custody 能力矩阵，不能把空间 owner 等同于代管人；缺少合法确认主体时保留 pending，绝不以空确认集合当作全部同意。已有 `connections` 命令只在语义完全匹配时复用，其 elder/younger 不足以表达收养/继亲时禁止强行映射。
- 同意/反对通过关联提案命令；使用 `provenance=agent_proposal`，最终 actor 是实际确认者；模型文本永远不是 raw 用户声明。

所有写命令复用外层 `command_transaction(immediate=True)`，内部可组合服务不提前 commit；FSM `transition_source_fact` 只是状态机，不是授权层，不允许路由直接调用来跳过确认资格。

## 通知/UI

Notification 增 suggestion_id FK 和受控 kind，唯一 `(recipient,space,suggestion)`，title 为固定模板，状态读取领域对象实时投影。当前 action_card 通知生成与 read/read-all 保持同一实现。摘要不复制未经校验 rationale，未知端点/失效引用在序列化前过滤。
新增建议列表/详情确认弹层，复用 shell 和通知入口；与确定性 family-recommendations 分开展示“待核实”，不在家谱画布画实线。通知终态只读，read 与 submit 两个动作分开。

## 迁移与回滚

旧候选保留内部记录，不默认标为可审核；后续新 job 生成完整证据才投影。新增表/索引/枚举迁移跟随 assist/projection，更新 conftest 清理顺序；停用审核 UI/API 后核心卡片仍正常，已提交提案仍可通过既有流程处理，不能因回滚丢失确认。
