# FamilyGraph V2.0 Foundation：身份、空间、隐私与数据权利

> 前置：v1 已完成；本任务是所有 Agent 子任务的硬依赖。当前无真实数据，不需要生产迁移兼容。

## Goal

先把 Agent 会依赖的身份、角色、空间、确档、可见性、数据权利和领域命令边界改造成唯一、可测试的基础合同，使后续 Pi Runtime 无法继承 v1 的全局 `is_admin`、直系边自动 full 或 owner 删除级联空间等旧语义。

## Requirements

### F-1 身份与确档

- Account 为认证主体，PersonProfile 为人物档案，二者生命周期分开但建档后保持明确关联。
- Account：`managed → claimed`；Profile：`provisional → identity_confirmed`；外部事实：`proposed → confirmed | disputed`，状态转换单向且审计。
- 首次登录先确认“这是我”，再审核名字、与创建者的关系及既有可选资料；未完成 identity_confirmed 不具备推荐资格。
- 建档表单名字和关系必填；其他字段可空、可动态添加；自由描述保留作者、原文、时间和 scope，但不自动成为正式事实。

### F-2 角色与管理员邀请

- `platform_operator` 仅管理系统代码、Provider、工具白名单和安全策略；默认无家庭数据读取权。
- 空间角色为 `space_owner`、`space_admin`、`member`，可有多个管理员；Household 可另有 `guest`，guest 不获得 household_detail。
- 平台运营者可生成短期、单次、可撤销的 owner onboarding link；兑换后为账号创建独立 LineageSpace 并授予 owner，不授予 platform_operator，也不连接其他管理员的空间。

### F-3 空间与人物引用

- `spaces.kind = household | lineage`；PersonalFamilyView 为派生投影。
- 创建他人时选择 no-space/household/lineage；选空间只创建 `space_profile_ref` 最小节点，provisional 人物不是 SpaceMember。
- 关系、配偶、管理员关联都不自动合并空间；桥边和共同 HouseholdSpace 均为显式对象/流程。
- owner 退出、删除或注销前必须移交；无合格继任者时进入显式终止流程，不允许数据库 FK 静默删空间。

### F-4 统一 VisibilityPolicy

- 统一输出 `self_private | household_detail | lineage_summary | none` 及字段级 disclosure，不让调用方自行拼规则。
- Household active member 可见家庭详情，但凭据、私人会话/记忆、未公开关系、健康、住址等高敏感字段排除。
- Lineage 只见必要字段和本人公开类别；全局公开偏好可被逐空间覆盖，默认不公开。
- 直系关系但不同 Household 不再自动 full；pending、guest、provisional 只见对应最小化信息。
- 未成年人默认最小披露；精确生日、住址、学校、联系方式、私人描述等不能因 lineage、Agent 或 operator 身份自动开放。
- Profile、图、附件、搜索、统计、导出、Agent/RAG 查询共享同一策略和 IDOR 矩阵。

### F-5 数据权利

- 本人可申请结构化导出、资料更正、删除/注销；所有异步结果继承 VisibilityPolicy 并有过期下载与审计。
- 删除、撤权、争议会传播到缓存、附件、DerivedFact、RAG/搜索索引和 Agent 会话投影；具体后续投影在相应任务实现，但 Foundation 定义事件合同。
- 认领争议保留 evidence、状态、双方最小披露和平台人工兜底；平台运营者处理争议需 break-glass 原因与完整审计，不因此获得日常浏览权。
- owner 移交、profile custody 移交和 Account claim 是三个不同流程，不混用。

### F-6 领域命令边界

- 将建档、档案修改、空间变更、关系请求、附件等 API 组合事务抽成可被 HTTP 与未来 Agent 工具共同调用的 application/domain command。
- 每条命令在同一短事务完成授权、FSM、写入、DomainEvent 和 audit；外部网络不进入事务。
- 不改变 v1 已有认证安全、备份和附件校验基线，除非新合同明确加强。

## Acceptance Criteria

- [ ] AC-F1：空库迁移后 Account/Profile/Fact/Space/Role 状态机和约束可由后端测试逐项验证。
- [ ] AC-F2：provisional 人物只能以最小节点出现，不能成为 SpaceMember、不能进推荐资格查询。
- [ ] AC-F3：owner invitation 单次、过期、撤销、重放和越权测试通过；兑换者只拥有新空间权限。
- [ ] AC-F4：同 household、同 lineage、直系跨 household、guest、pending、provisional、minor、operator、无关用户的授权矩阵逐行 IDOR 通过。
- [ ] AC-F5：owner 删除被阻止并引导移交；移交审计完整，空间与成员不会被 FK 静默删除。
- [ ] AC-F6：导出/更正/删除/争议流程可观察，删除或撤权事件能驱动后续投影失效。
- [ ] AC-F7：HTTP 路由与领域命令使用同一授权/事务实现，未来 Agent 不需要复制 ORM 组合逻辑。
- [ ] AC-F8：从空数据库完整迁移、v1 回归、online backup/restore 通过；仓库中没有生产数据回填或双写分支。

## Out Of Scope

- 本任务不接 Pi、不创建 Agent Session/Run，不实现关系称谓推理、ActionCard 或 RAG。
- 不做真实生产数据迁移；当前没有部署、用户或业务数据。
- 不允许 platform_operator 通过普通管理 UI 浏览家庭数据。

## Blocking Open Questions

无。
