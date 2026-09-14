# 复查问题台账

## 口径与总判定

日期：2026-09-14；基线：`bd899b98871c02ad97b7791a051f126d671bfefb`。B `078f2e3` 与 D `bd899b9` 的提交存在；“已经提交/曾有全包绿灯”与“所有需求已通过”不等价。本次新增 **20 组未闭合合同：P1 12 组、P2 8 组；18 组已在合成环境复现，2 组静态确认未接线**。优先级用于修复排序，不是漏洞评分或线上事故等级。

每项原始输入路径、同步点、完整 file:line、用例名、命令和边界见 [B 独立报告](b-integration-check.md) / [D 独立报告](d-integration-check.md)。主线程抽查了真实鉴权/调用点、引用认证和四个读取出口、ContextBuilder、document 索引、chunk 写入、租约函数、maintenance commit、0045 downgrade；结论来自源码与实证共同支持。源码锚点均指上述提交，不是尚未合入的 main 同名文件。

## B：执行身份、引用和检索

| ID / 级别 | 事实与影响 | 最小修复方向 | 原需求 / 修复验收 |
|---|---|---|---|
| B-I01 / P1 / 复现 | 鉴权后另一真实 Session 将 attempt 1→2，旧 context/events/heartbeat/settle/tool 仍 200；回答/工具落库，旧执行结算新 attempt。`api/internal_agent.py:208,470,545`；`agent_queue.py:320,382` | 签名 attempt 透传到 writer/CAS，Run/Job 双 attempt 与绑定同事务核验；provider admission 同步补测 | MR-14/24；B-AC6/8；F-01 |
| B-I02 / P1 / 复现 | 删原 chunk、改文、改 index_version、改 source_revision 后旧引用仍可认证。`context_builder.py:28,39,256`；`agent_events.py:321`；`api/agent.py:147` | 保存精确 document/chunk/revision/version/hash 并统一解析，之后再做当前来源授权；旧合法片段可读 | MR-14；B-AC4/6/8；F-02 |
| B-I03 / P1 / 复现 | 有效 internal run token 自报假 citations/摘录，SSE 原样输出，fallback 却为空。`agent_events.py:207`；`api/agent.py:641` | 公开保留字段由服务端独占；SSE 在短会话内作同一当前读者投影，不依赖客户端补取覆盖假值 | MR-14/24；B-AC5～8；F-04/05 |
| B-I04 / P1 / 复现 | 来源撤销后浏览器 history 已显示 []/1，下一 run 的 internal history 仍含旧 source_id/handle。`api/internal_agent.py:489` | internal 出口也投影，或只保留允许的正文；保留 C 的正常文字恢复 | MR-14；B-AC6/8；F-04 |
| B-I05 / P1 / 复现 | 同 attempt 关 RAG 后仍返回原 block；两个并发首次 GET 返回 [200,500]、唯一键异常。`context_builder.py:163,177,236` | writer 内复验身份后复用/创建；持久失效状态；原 included 集精确重读 | MR-14/24；B-AC6/8；F-03 |
| B-I06 / P2 / 复现 | 实际 context budget=2000，却纳入包装估算为6270的六块中文。`context_builder.py:151,240,267` 仍 len//4 | 实际 Builder/下发包装/审计字段复用同一保守估算器；整块有界排除 | MR-10；B-AC4；F-06 |
| B-I07 / P2 / 复现 | 纯英文 FTS 前排依赖失效时 limit=1 无结果、limit=20 有合法下一项。`memory_rag.py:1025,1028` | 稳定排序、总扫描上限内继续补足；统计拒绝与停止原因，权限不放宽 | MR-03；B-AC3；F-06 |
| B-I08 / P2 / 静态 | 生产 planner 仅 raw 参数，ContextBuilder 只传最新 user；无历史 anchor/歧义字段。`rag_query.py:118`；`internal_agent.py:451,466` | 有限同会话唯一锚点与歧义降级；新增能区分关键词碰巧命中的正反例 | MR-04；B-AC2；F-06 |
| B-I09 / P2 / 复现 | 早先无关会话 user 使用相同幂等 key，目标 history 有1条引用，fallback 错选 user 返回0。`api/agent.py:559` | 同时限定 run.session_id、assistant role、服务端 key 和事件类型 | MR-14；B-AC7/8；F-04 |
| B-I10 / P2 / 静态 | sidecar→EventIn 没有内部 context_reference；指纹仅 type/public_payload，服务端自行选当前 build。`schemas/agent.py:115`；`agent_events.py:70,75,283` | 同步内部 schema/adapter，显式 build/attempt/used_handles 参与原请求指纹并受认证，内部字段不公开 | MR-14/24；B-AC5/6/8；F-01/03/04/05 |

以上源码前缀统一为 `backend/app/`。B-I08/I10 的否定检索范围是 backend/app、backend/tests、agent/src、agent/test、shared 的生产接线与协议使用点；没有用不存在的测试报告证明它们。

## D：索引与维护

| ID / 级别 | 事实与影响 | 最小修复方向 | 原需求 / 修复验收 |
|---|---|---|---|
| D-I01 / P1 / 复现 | 双 Session 同源物化产生2份 document；0045 接受存量重复及 revision/source_revision 冲突。`models/rag.py:60`；`memory_rag.py:671`；0045 `:33` | 非破坏元数据预检、真实唯一约束与镜像一致性；冲突读取同份结果/明确拒绝 | MR-13/25/24；D-AC2/8；F-07 |
| D-I02 / P1 / 复现 | 同 chunk ID/revision/version 文本被覆盖；预置冲突 staging 块直接被激活。`memory_rag.py:765`；`rag_maintenance.py:303,313` | 相同定位内容不可变；验证目标完整集合/hash/status，冲突保留旧片段 | MR-09/14/25；D-AC2/6；F-02/07/09 |
| D-I03 / P1 / 复现 | 旧 Session 抢走新 lease，cursor 3→1、attempt仍1；旧策略还能推进 future policy。`rag_maintenance.py:93,108,225` | attempt/owner/expiry/round/policy/target 的数据库条件写，失配整体回滚 | MR-13/25/24；D-AC5；F-08 |
| D-I04 / P1 / 复现 | 真实 tick 捕获最终失租异常后仍 commit，物化已持久化而cursor和counter为0。`maintenance.py:109,116,121` | 批次独立事务/完整 savepoint；捕获前回滚全部物化/失败登记/进度 | MR-13/25/24；D-AC5；F-08 |
| D-I05 / P1 / 复现 | RAG off + 他人有效 lease 仍能 stage；另一 Session 撤销后仍写active新块/换指针；普通batch最后未复查开关。`rag_maintenance.py:164,259,331,340` | stage 共用真实租约；最终来源/开关/版本条件校验，与块和指针原子提交/回滚 | MR-13/15/25；D-AC4/5/7；F-08/09 |
| D-I06 / P1 / 复现 | stage v3 后命中1→0，下批次v3→v2；进程目标升级成v3时stage(v3)却422。`rag_maintenance.py:272`；`memory_rag.py:716,852,1000,1452` | 统一部署目标/活动指针/读取/ensure语义，普通维护不自行降版 | MR-13/14/25；D-AC5/6；F-09 |
| D-I07 / P2 / 复现 | 已验证恢复来源缺FTS仍记already_current、命中0；三块缺一块后仍2/3。`memory_rag.py:1453,1462` | 检查预期块集合及搜索投影，恢复缺项/明确失败，既有片段不改文 | MR-13；D-AC1/5/6；F-10 |
| D-I08 / P1 / 复现 | 0045降级删除保存副本依赖的旧chunk；再升级后Memory引用JSON还在但读取unavailable。0045 `:83,88` | 任一DROP/DELETE前做无损兼容/依赖预检；不能保持则拒绝，不静默删除 | MR-14/17/25；D-AC6/8；F-11 |
| D-I09 / P2 / 复现 | 没有轮次固定上界，每tick持续新增后round一直0，恢复的低ID仍无document。`models/rag.py:138`；`rag_maintenance.py:169,215` | 持久固定水位，每轮有限；下一轮覆盖低ID，保留退避 | MR-13；D-AC1/5；F-10 |
| D-I10 / P2 / 复现 | unverified来源被FTS repair计为合法重建1行；search仍正确返回0。`memory_rag.py:1414` | repair复用来源合法性，不把查询时再过滤当修复合法；不修改业务状态 | MR-13/25；D-AC3/6；F-10 |

D 源码前缀为 `backend/app/`；0045 指 `backend/migrations/versions/0045_rag_index_lifecycle.py`。canonical 唯一键的否定检查覆盖全部 `backend/migrations/`，并以真实迁移的 PRAGMA 结果核实。stage v3 是合成版本合同实验，未声称存在已上线 v3 算法。

## 已修复的独立 C 发现

**C-I01 / P2 / 已修复但未进入本基线**：实际 Pi 0.84.3 经 overflow→summary→retry(stop) 已成功，worker 却按第一次 Provider 错误结算 failed。`agent/src/worker.ts:221` 的修复只在完整 assistant stop 后清除局部旧 Provider 错误，保留取消/失租/policy 的优先级。提交 `8e91c42`；实际 SDK 独立探针与113条完整 sidecar 测试通过。详见 [C 检查](c/check.md)、[C 实施](c/implementation.md)。

仍不承诺任意超大请求能成功、跨 Run 持久摘要或真实模型摘要质量。280000字符当前输入已用持久回归证明完整且只出现一次，压缩重试后仍超限则明确失败。

## 已证明的正面行为

- 冻结中文核心16/16、英文2/2、独立扩展7/10；见 retrieval/ 下的数据与报告。
- 已提交事件在丢响应后来源撤销，同原始请求仍返回 duplicate，记录/指纹不被改写。
- 完整public_payload恰好16384字节可保存，增加1字节拒绝；中文/emoji/转义/role/web均计入，正文/web保留，额外引用可补取。
- 浏览器history及普通fallback的来源撤销遮蔽有效；internal和SSE的缺口分别记录，不能概括为“所有历史泄漏”。
- 真实RAG-only循环在平台晚开启后补齐；未知tombstone不自动复活；临时读者失权与根来源永久失效分开。
- 现有真实API smoke 56/56；新增真实listener+SidecarWorker/Pi两轮、来源撤销与丢响应重试50/50。正常链通过不覆盖旧attempt/恶意内部字段/并发版本等负例。

## 影响边界与未测

B-I03 的调用者持有效internal run token，不是普通浏览器直接绕过认证；B-I09 只证明错选导致引用缺失，未证明他人来源泄露。B-I02 的四类存储变更是受控诊断，未声称都有普通UI写入口。D-I05 的撤销后新块仍被document状态挡在检索外；D-I10同样没有观察到检索越权。

代码中的 context blocks 正文留存缺乏精确定位，设计建议改为可重验描述符；本轮没有据此宣称已发生正文泄漏，也没有删除已有快照。模型语义忠实度、生产负载/延迟、真实部署开关、真实外部推理和主库迁移均未测试。原 MR-23/26 的实证按 E 的历史版本与限制引用，本轮没有在累计基线重新执行 Steward 全链。
