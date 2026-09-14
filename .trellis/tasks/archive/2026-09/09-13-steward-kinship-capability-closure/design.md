# Design — Steward 称谓能力闭环

## 1. 责任和数据流

保留 SourceFact → relationship_resolver → Terms → PersonalFamilyView。增加带 viewer 上下文的呈现服务和受控称谓投影，不增加第二套亲缘算法。

```text
领域事件 / 周期扫描 → Steward core
  → 获准路径 + 四级词条 + 长幼/长链规则
  → 适用且有效的个人自动投影 → 保存 PFV
  → 发现改善，生产可选 term_preference
  → 登记 terminology 工作
既有 assist 泵：预留 → 网络 → 校验 → 版本重验 → 写回个人投影/建议
  → 一次幂等的后台个人视图刷新
认证账号 → 同源呈现服务 → 通知 / 建议详情 / 档案 / 家族树
主动保留 → set_personal_term；恢复原叫法 → 抑制自动投影并刷新
```

显示文字不决定事实。candidate 继续只产原子线索，terminology 才能产经校验的称谓。SourceFact 枚举保留内部用途。

## 2. 统一呈现合同

A 定义版本化 KinshipPresentation，复用于 Suggestion、PFV 推测详情和通知；保留现有 term 等兼容字段，详见 [A design](../09-13-steward-kinship-presentation/design.md)。

- viewer 由认证身份派生，不接受客户端指定他人视角。
- 个人摘要明确 reference=当前用户；结构关系明确自身两个端点，不能套个人摘要。
- 已有当前 PFV 优先消费。候选的未确认一步经既有 PathStep/Terms 生成带“可能”的方向表达，不伪装 confirmed PFV。
- 无当前个人路径时保留获准姓名和候选方向，返回 availability；不能使用旧授权快照或 raw enum 兜底。
- 来源统一为 personal/space/locale/system/derived/structural/steward；steward 另带 origin=deterministic/model。
- evidence 区分可验证相关路径与未证实模型线索；旧整空间快照不算相关证据数量。
- requires_action 与未读、可选偏好、模型来源分别表达。

前端只渲染服务端结果及允许动作；旧载荷安全降级，不恢复前端亲缘词典。A 先修 derived 缺口，B 增加 steward 来源后同步全部消费者。

## 3. 自动结果与显式偏好

优先级：个人词条 → 生效空间词条 → 有效 Steward 个人投影 → 现有 locale/system、长幼、长链及结构回退。自动投影只能改善已绑定路径的显示，不作为关系依据。

StewardTermProjection 是可重建缓存，不是新词典。按 space/account/root/target 绑定，保存 baseline、候选 term、路径摘要、输入与规则版本、origin、model_call_id、revision、应用/抑制状态和有限反馈。详见 [B design](../09-13-steward-terminology-autonomy/design.md)。TermEntry/TermUsage 和两人晋升规则保持原真源。

新称谓建议默认不逐条写通知，在 KinshipTermPanel 展示；旧称谓通知也显示为无需处理。可选保留继续走本人偏好，不新增审批页面或通知聚合模型。

## 4. 模型输入与语义

一次 attempt 只有一个 viewer 和一组有界目标，输入只含获准路径、去标识代号、当前叫法、允许的同义表达、可公开的相对长幼、本人相关用词/拒绝记录；不发送姓名、生日原值、其他人偏好或整空间事实。

输出包含目标代号、输入摘要、概念码、term、有限 reason_code。服务端从真实路径核验方向、已知性别、亲属亚型、长幼和语义，不信模型自报的概念码。利用现有词条/词素和合法路径组合验证自然词与组合叫法；无法解析的新造词放弃，不转成待批准卡。

intake_extractor.code_matches 只可部分复用：哥哥/弟弟和伯/叔在现有码中合并，不能只比较码就通过，必须核验 qualifier。可抽取纯词素元数据共用；不复制亲缘算法，也不让 Terms 循环依赖写命令。

## 5. 调度与失效

复用 batch/call 的事务、预算、Provider、recovery；显式接入注册、预留、prompt 重建、验证和写回。未知 kind 明确拒绝，不继续落入 explanation 默认分支。

输入摘要覆盖 viewer/root/space、目标集合、路径/source revisions、相对长幼、适用词条 revisions、本人反馈、规则/prompt 版本。预留、发送、写回重验同一合同；不能拿只对 candidate 生效的整空间 facts fence 代替。本批可以沿原整体结算保守放弃，但不能删除其他 viewer 先前已保存的有效投影；不新建部分成功状态机。

输出变化仅触发一次幂等刷新。语义输入不含新投影自身 revision，避免自激调用；无变化、无合格输出和同证据拒绝均记已检查/抑制，不在每次扫描重新发模型。首次优化分批推进，确定性显示不等待。

PFV 新鲜度包含适用投影 revision/有效性；撤权或词条改变即禁用旧结果。关闭 terminology 后回退确定性显示，用户已主动保存的词条继续生效。普通 GET 保持只读和零模型调用。

## 6. 治理、兼容和迁移

新增 STEWARD_ASSIST_TERMINOLOGY、平台与 Steward 空间字段，复用最新 DB ∧ environment、空间选择、Provider 和云/本地约束。默认关闭，不改线上配置；旧客户端遗漏字段时保留现值。

共享每 job 总预算；B 固定目标上限与公平调度，不能让大量 explanation 把 terminology 永久挤出。健康/统计识别只有 terminology 开启的情况。

A 采用增量响应；B 新迁移扩 assist CHECK、配置与个人投影。编号在串行集成点分配，禁止编辑旧 0034/0041 迁移。先隔离 upgrade head 并验旧数据。

## 7. 文件所有权

A、B 共用 suggestions/PFV/Terms/类型/UI，串行实施。topology 已在规划期间集成并归档，其真实结构与几何作为基线保留；本任务沿已有 topology_edges 接入显示合同，不重做布局。

记忆 E 保留 MR-23/MR-26 复现、shared RAG 和一般反馈排序。称谓生产/反馈消费不再作为 E 中未授权能力。具体职责修改见 [任务对齐](research/task-alignment.md)。

## 8. 验证与回退

验证从真实 job/fake transport/API 进入，覆盖多 viewer、同义词、长幼未知、继养监护、长链、撤权、旧通知、重放、预算和恢复。真实模型质量、线上状态另记，不能复用旧 39 pass 作本期验收。

回退先禁用 terminology 调用/投影读取，再由后台恢复确定性视图；自动产物不污染 TermEntry/SourceFact。使用前进式代码恢复，保留迁移和历史，不重写共享分支。完成后串行集成、归档和清理。
