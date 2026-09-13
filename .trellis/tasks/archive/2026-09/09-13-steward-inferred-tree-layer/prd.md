# 管家推测层：家族树自动补全推测关系与称谓

## Goal

把管家的产出从「建议卡审核流」升级为「推测层直接上树」：管家基于已确认事实与既有候选
来源推断缺失的原子亲属关系，以**推测边**（虚线 + 「推测」角标 + 确定性解析的称谓）直接
呈现在家族树上；成员在树上一键确认/驳回，确认后按现行 consent 合同转正为 confirmed 事
实。推测层永不直接改写 confirmed 事实。

## 背景

- 现行架构（09-11 收口）：steward core 只产出建议（ActionCard / StewardSuggestion），
  LLM 候选只进内部池（`StewardLlmCandidate`）→ 投影为审核队列（`StewardSuggestion`），
  一切等人工处理；家族树（PersonalFamilyView，下称 PFV）只从 confirmed SourceFact 确
  定性解析（`relationship_resolver` 路径枚举 + TermRegistry 称谓）。
- 用户诉求（2026-09-13）：开启管家并配置模型后，期望树自动补全（称谓、树状逻辑映射），
  而不是先处理通知卡片树才变化。当前产品形态与预期根本不符。
- 用户已定关键决策：**推测层直接上树**（虚线+角标区分，一键确认/驳回；不自动写
  confirmed 事实，可整体重算回滚）。

## Requirements

- **R1 开关治理（fail-closed）**：新增平台级环境开关 `STEWARD_INFERRED_TREE_ENABLED`
  （默认关）与空间级开关 `inferred_tree`（模型设置面板，默认关）；生效语义 =
  平台 AND 空间。空间级开关打开但平台级关闭时，面板必须显示可解释的
  「平台级未开启」提示（与 09-13-steward-assist-platform-switch-admin 的提示机制一致）。
- **R2 推测边来源（v1）**：以现有 LLM 候选池（candidate 辅助产物，节点代号白名单）为
  唯一自动来源，经管家作业投影为「推测边」（origin=llm）；v1 不引入新的模型调用形态，
  不做确定性结构补全规则（列为后续项）。intake_extractor 的 supported 提案是否并入推测
  层列为开放问题（见 Notes）。
- **R3 树上呈现**：PFV payload 新增推测边部分（只含安全显示字段）；推测边为**单跳原子
  关系**（与 LLM candidate 允许的 kind 集合一致），称谓由确定性引擎按该单跳解析，
  绝不由模型直接产出称谓文本；推测边可把「可见但暂无 confirmed 路径」的成员带上树
  （inclusion_reason_code=inferred_path）。
- **R4 树上操作**：推测边/节点上提供确认与驳回：
  - 确认：viewer 是该关系的有权当事人（端点本人或合法代管人，沿
    `relationship_proposals` 资格判定）→ 直接转正（经现行 confirm 合同）；
    无权 → 代为提交关系提案（202 语义），推测边标注「已提议，待对方确认」。
  - 驳回：记录 rejected 状态 + 证据哈希冷却（同证据不重复上树），可撤销驳回恢复
    proposed。
- **R5 生命周期**：推测边状态机 proposed / rejected / confirmed / superseded；
  证据（confirmed facts 摘要 hash）变化 → 旧边 superseded 并按新证据重新投影；
  confirmed 后推测边消亡（其信息已由 confirmed 边承载）；空间成员不可见/删除 →
  随作业清理。
- **R6 隐私与审计**：LLM prompt 侧红线不变（节点代号、confirmed 白名单、无真实姓名，
  `steward_guard` 出口校验）；推测边 API 只返回安全显示字段；状态转换写 domain event
  （新 `steward.inferred_*` 命名空间）+ audit。
- **R7 一致性**：PFV `COMPUTATION_VERSION` 升版触发全量重算；推测层开关关闭时树
  payload 与现行为完全一致（回滚形态 = 关开关）。

## Acceptance Criteria

- [ ] 平台级或空间级开关任一关闭时：PFV payload 无推测边、无新增字段语义变化、
  管家作业不投影推测边；两开关均开时投影发生。
- [ ] 空间级开 + 平台级关：模型设置面板显示平台级未开启提示（可解释、可行动）。
- [ ] LLM 候选经作业投影后，树上出现虚线推测边，label 为确定性解析称谓，节点卡片带
  「推测」角标；仅经推测边可达的节点以 inferred_path 身份出现。
- [ ] 有权当事人一键确认后：生成 confirmed SourceFact，推测边消失，confirmed 边与称谓
  出现；无权 viewer 确认 → 生成关系提案（202 语义）而非直接确认。
- [ ] 驳回后同证据哈希不再重新投影；证据变化（新 confirmed fact）后旧边 superseded、
  新边可重新出现；confirmed/superseded 边不再渲染。
- [ ] 全部新端点：revision CAS + Idempotency-Key 幂等；错误码沿用统一错误结构；
  状态转换落 domain event 且含审计字段。
- [ ] 迁移在隔离数据库 `alembic upgrade head` 通过；受影响后端测试
  （steward/PFV/resolver/guard/suggestions 相关）与新增测试全绿；
  `ruff check . && ruff format --check . && mypy app && pytest` 通过。
- [ ] 前端 `npm run lint && npm run type-check && npm test && npm run build` 通过；
  树画布（称谓/自由画布两种模式）对推测边渲染与操作有组件测试。

## 非目标（v1 明确不做）

- 自动写入 confirmed 事实（高置信自动转正）——用户已明确选择推测层形态。
- 多跳推测链（推测边互相串联参与路径枚举）——单跳呈现，避免不确定性复合。
- 模型直接产出称谓/派生概念文本（现行红线保持）。
- 管理端（system-admin-frontend）新增治理界面——平台开关沿用 env；管理端治理归
  09-13-steward-assist-platform-switch-admin 一并处理。
- 确定性结构补全规则（如「配偶的已确认子女 → step_parent 提案」）——列为后续项。

## Notes

- 开放问题（已定案，见 design.md §3）：intake_extractor 的 supported 提案 v1 **不并入**
  推测层——其「最后一跳无对应人物」语义需要新建人物流程，不是既有可见成员间的单跳
  边；推测边 origin 字段预留 `intake`，待人物创建流程设计后并入。
- 执行顺序：**09-13-steward-term-autofix（称谓修复）先行**——用户 2026-09-13 确认
  「你的父亲的女儿」式回退称谓是首要痛点；本任务在其后进行。
- 相关任务：09-13-steward-assist-platform-switch-admin（平台开关治理与面板提示）、
  09-13-agent-latency-tuning（辅助延迟背景）。
- 过程证据：2026-09-13 用户反馈「开了管家+配了模型，树没有任何变化」，现行建议卡流
  与用户预期不符（会话记录）。
