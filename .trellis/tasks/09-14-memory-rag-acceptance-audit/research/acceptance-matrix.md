# 验收回填矩阵

## 原父任务 AC 的当前判定

本表更新2026-09-14的整体验收判断，不覆盖此前命令输出。基线bd899b9；C补充修复8e91c42单独记录。父任务保持in_progress；本复查任务保持planning。

| 父 AC | 判定 | 依据与剩余工作 |
|---|---|---|
| AC-01 完整问题/方案映射 | 规划证据齐全 | 原26项保留，本次20组增加证据/所有者/修复验收；不是业务已修复 |
| AC-02 Memory创建确认闭环 | 既有A验收保留；API链复核通过 | A的1050后端、56前端及旧API证据；累计56项API smoke、新50项正常链通过。未重新执行浏览器可视交互 |
| AC-03 权限、revision与来源生命周期 | **未闭合** | B-I01/I02/I03/I04/I05、D-I05；普通来源撤销与跨读者边界正例通过不代替竞争/精确引用 |
| AC-04 中文召回与反例 | **部分完成** | 核心16/16、英文2/2；扩展7/10。B-I07授权后补足和B-I08唯一追问未闭合；无依据查询的非空命中不是回答忠实度结果 |
| AC-05 Pi恢复/压缩 | C分支验证通过，累计补丁待纳入 | 初版C已在bd899b9；补丁8e91c42使overflow成功后正确结算，113 tests+独立SDK探针通过。累计候选应重验相关包 |
| AC-06 索引幂等、维护、版本稳定 | **未闭合** | D-I01～09；RAG-only晚开启及未知tombstone正例通过 |
| AC-07 可信引用、所有读取面与字节上限 | **未闭合** | B-I02/I03/I04/I09/I10；整个16384字节边界与原请求重试对照通过，正常SSE+fallback链通过 |
| AC-08 有效开关与治理边界 | **部分完成** | A开关/门禁、D晚开启正例保留；B-I05/D-I05关闭后重复读或提交仍不符合合同；未查线上开关 |
| AC-09 能力评估与诚实边界 | E研究交付保留 | E十二项决定、MR-23/26实证和既有后续owner保留；不是新能力生产验收 |
| AC-10 相关检查与真实合同集成 | **部分完成，不能整体通过** | 已有56项API smoke及新增50项真实listener+Pi正常链；新安全/并发反例未修复，C补丁未纳入累计代码。隔离验证无需先合并main |

## 新修复门槛 F-01～12

“通过对照”只能证明该行的一部分。完整通过须修复后运行目标行为的回归和真实链，不按测试总数推断。

| 门槛 | 当前状态 | 对应探针/证据 | 后续责任 |
|---|---|---|---|
| F-01 真实执行栅栏 | 失败；provider仅静态 | B `signed_attempt`五入口；补provider admission、Job attempt不一致、bool/非正attempt | B |
| F-02 精确引用 | 失败 | B `requires_exact_original_chunk`四变更；D旧版依赖正例、同块覆盖反例 | B认证 + D不可变写端，串行 |
| F-03 Context复用/失效 | 失败 | B `repeated_context`、`concurrent_same_attempt`；补政策变化、持久失效后恢复 | B |
| F-04 读取投影与定位 | 失败 | B `sse_does_not_trust`、`internal_history`、`fallback_is_scoped`；新正常链通过 | B |
| F-05 原请求幂等与字节 | 部分通过 | B `source_revocation_retry`、`exact_16k_public`通过；内部reference未接线，须补身份/指纹异参反例 | B |
| F-06 实际预算/补足/追问 | 失败/静态缺失 | B `real_context_obeys`、`fts_refills`失败；冻结核心/英文通过；新增唯一与歧义前文对照 | B |
| F-07 规范身份/不可变块 | 失败 | D两个真实Session物化、同revision冲突、staging冲突、旧库重复/镜像迁移 | D |
| F-08 持久租约和原子提交 | 失败 | D旧Session回写/未来policy、真实tick最终拒绝、开关/撤销同步点 | D |
| F-09 版本接管 | 失败 | D stage后搜索1→0、下批回退、目标版本422、来源撤销后的新块 | D |
| F-10 有限巡检/完整性 | 失败 | D持续新到达饿死低ID、缺FTS/缺块、非法来源repair | D |
| F-11 无损迁移/降级 | 失败 | D真实保存rag_chunk副本，downgrade后片段丢失、再upgrade仍不可用 | D |
| F-12 累计验证 | 部分通过，尚不能放行 | A正常API、C独立SDK、新50项正常链保留；全部修复/C补丁合成候选后验收 | 父串行集成 |

## 本次规划交付 A-01～05

| 门槛 | 交付证据 |
|---|---|
| A-01 问题完整 | [台账](findings.md)、[B报告](b-integration-check.md)、[D报告](d-integration-check.md)、[C记录](c/check.md) |
| A-02 原问题和建议完整 | [覆盖与路线](coverage-and-roadmap.md)，链接原审计及E决定登记 |
| A-03 规划/清单校验 | [PRD](../prd.md)、[design](../design.md)、[implement](../implement.md)、两个jsonl、[校验记录](planning-validation.md) |
| A-04 证据可复现 | [验证记录](validation.md)、[制品哈希](artifacts.json)、probes/retrieval/smoke目录 |
| A-05 状态如实回填 | [父执行记录](../../09-13-agent-memory-rag-remediation/research/execution.md)的复查更新；B/D保持待修、C补丁单列 |

没有将“未合并main”当作隔离smoke的技术阻塞。只有后续合并、归档和清理需要满足各自授权/前置条件。
