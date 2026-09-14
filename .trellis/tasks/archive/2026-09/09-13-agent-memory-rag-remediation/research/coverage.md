# 发现、需求、任务与验收覆盖矩阵

本表与 [审计台账](audit.md) 一一对应，共 26 项。P1/P2/P3 是规划优先级；“已复现”仅限 [复现记录](evidence.json) 的隔离应用/假模型范围，不代表线上已发生。所有修复验收目前均为计划，不能因为规划完成标为已修复。

任务入口：[父 PRD](../prd.md)、[A](../../09-13-memory-contract-repair/prd.md)、[B](../../09-13-rag-retrieval-citations/prd.md)、[C](../../09-13-assistant-context-compaction/prd.md)、[D](../../09-13-rag-index-lifecycle/prd.md)、[E](../../09-13-agent-memory-capability-plan/prd.md)。V 编号对应 [验证计划](validation-plan.md)，AC 对应相应 PRD。

| 发现 | 优先级 / 证据等级 | 问题及处置 | 父需求 / 唯一主责 | 验收与验证 |
|---|---|---|---|---|
| [MR-01](audit.md#mr-01) | P1 / 已复现 | 新 UI 显式来源；旧无来源歧义请求保持拒绝 | R-01 / A | A-AC1/4/8；V-A01/02 |
| [MR-02](audit.md#mr-02) | P1 / 已复现 | raw_quote 映射、提交前 DTO、创建重试与确认唯一性 | R-01/07 / A | A-AC1/2/3；V-A01 |
| [MR-03](audit.md#mr-03) | P1 / 已复现；补足风险静态 | 短词后备、词法计划、有限补足；不放宽权限 | R-02/07 / B | B-AC1/3；V-B01/02 |
| [MR-04](audit.md#mr-04) | P2 / 静态确认 | 同 session 唯一追问锚点，歧义时不猜 | R-02 / B | B-AC2；V-B01/02 |
| [MR-05](audit.md#mr-05) | P3 / 能力缺口 | 对一次预取与主动检索工具做增益评估 | R-08 / E-O1 | E-AC1/2；V-E01 |
| [MR-06](audit.md#mr-06) | P3 / 未接入；默认提取已复现为空 | 分开显式保存和 opt-in 自动候选；不自动确认 | R-08 / E-O2/3 | E-AC1/3；V-E02 |
| [MR-07](audit.md#mr-07) | P3 / 静态未接入 | 授权文档上传、解析、来源与撤销闭环评估 | R-08 / E-O4 | E-AC1/3；V-E02 |
| [MR-08](audit.md#mr-08) | P3 / 预留字段 | embedding/混合/重排对照；收益不足保留词法 | R-08 / E-O5 | E-AC1/2；V-E01 |
| [MR-09](audit.md#mr-09) | P2 / 静态实现限制 | 确定性句段切块与有限重叠；仍明确索引摘要范围 | R-02 / B | B-AC4；V-B01/02；D 消费版本合同 |
| [MR-10](audit.md#mr-10) | P2 / 静态实现限制 | B 负责 RAG 子预算；E-O6 负责全请求方案，两项分别验收 | R-02/08 / B（局部），E（总量） | B-AC4、E-AC4；V-B02/E03 |
| [MR-11](audit.md#mr-11) | P1 / 已离线手动复现 | 同一 Pi manager 预填历史；补真实自动触发回归 | R-03/07 / C | C-AC1～4/6；V-C01/02/03 |
| [MR-12](audit.md#mr-12) | P3 / 当前边界 | C 保留文字恢复；E-O7 独立评估持久摘要与来源失效 | R-03/08 / E（扩展），C（边界） | C-AC5、E-AC3/4；V-C01/E03 |
| [MR-13](audit.md#mr-13) | P1 / 已复现 | 先修生命周期，再接 RAG 独立有界维护 | R-04 / D | D-AC1/5/7；V-D01/02/04 |
| [MR-14](audit.md#mr-14) | P2 / 静态链路缺口 | 有效 build 绑定、引用事件/历史、撤权遮罩与重放 | R-05 / B | B-AC5～8；V-B03/04 |
| [MR-15](audit.md#mr-15) | P2 / 状态/文档治理 | D 保留 effective AND；E 区分实现/接线/开关/验证，引用原 admin 任务 | R-06 / D（RAG 状态），E（能力说明） | D-AC7/8、E-AC7/8；V-D04/E04 |
| [MR-16](audit.md#mr-16) | P2 / 静态确认 | Memory off/RAG on 禁用保存；状态失败不伪装关闭 | R-06 / A | A-AC7；V-A03 |
| [MR-17](audit.md#mr-17) | P3 / 产品边界 | A/D 保证来源读取/索引失效；E-O9 定义历史/物理保留策略 | R-06/08 / E（政策），A/D（现有边界） | A-AC5/6、D-AC3/4、E-AC3；V-A02/D01/E02 |
| [MR-18](audit.md#mr-18) | P3 / 功能缺口 | 编辑/scope 用新 revision 和必要确认；独立评估 | R-08 / E-O8 | E-AC1/3；V-E02 |
| [MR-19](audit.md#mr-19) | P3 / 明确延期 | 交接共享检索合同与六类对照；现有 followups 拥有接入 | R-08 / E-O10（交接） | E-AC1/7/8；V-E04 |
| [MR-20](audit.md#mr-20) | P3 / 当前边界 | 进度/去重不称为学习；反馈排序须对比确定性基线 | R-06/08 / E-O11 | E-AC2/6/8；V-E01/04 |
| [MR-21](audit.md#mr-21) | P3 / 未接入 | 只有明确消费者才启用偏好投影；不复制 TermRegistry | R-08 / E-O11 | E-AC6；V-E06 |
| [MR-22](audit.md#mr-22) | P3 / 已知范围限制 | 同证据 dismissed 保持；到期重现需产品选择和冻结时钟 | R-08 / E-O12 | E-AC5/6；V-E05 |
| [MR-23](audit.md#mr-23) | P2 / 静态缺口待多 job 复现 | 从真实候选链验证新证据阻断；结构身份与相关证据版本分开 | R-07/08 / E-O12 | E-AC5/6；V-E05 |
| [MR-24](audit.md#mr-24) | P1 / 验证缺口 | 所有子任务补实际 API/Pi/跨端/权限链，不能仅靠 mock 绿 | R-07 / 父任务整合，A～E 各自执行 | 父 AC-10；全部 V，尤 V-I01 |
| [MR-25](audit.md#mr-25) | P1 / 规划新增静态通路 | tombstone 防复活、唯一身份、同版本稳定 chunk | R-04/07 / D | D-AC2/3/4/6/8；V-D01/03 |
| [MR-26](audit.md#mr-26) | P2 / 无生产调用的条件风险 | 混合键族复现；仅重建本键族，同事件集幂等，保留他类冷却 | R-07/08 / E-O11 | E-AC5/6；V-E06 |

## 所有权解释

斜线分工表示不同交付切面：MR-10 的子预算与全请求、MR-12 的恢复边界与持久摘要、MR-15 的 RAG 状态与能力说明、MR-17 的受控读取失效与保留政策。它们不是让两个人同时改同一函数；具体文件仍按父 implement 的 A→C→B→D 串行。

MR-23/MR-26 的 P2 不因 E 整体 P3 而降级；E 先给出完整生产链复现与最小修复设计，再明确一个 Steward 实施任务。当前不复制既有 shared RAG/admin 所有者。规划审阅中新发现的设计合同问题列在 [规划校验记录](planning-validation.md)，不冒充已复现生产问题或另增审计编号。
