# 验证计划

本文件规定实施验收，不是已通过报告。既有结果只有 [修复前证据](evidence.json)：backend 147、sidecar 45、frontend 19，共 211 pass，以及隔离 API/RAG/Pi 探针。规划轮只做任务/文档校验，结果单独见 [planning-validation.md](planning-validation.md)。

## 1. 通用执行合同

- 数据全部为合成账户、空间、文本和事实；使用独立 DATA_DIR 与实际 Alembic schema，禁止复用开发/线上 SQLite 或直接复制运行中的 WAL 主库。
- 使用实际 FastAPI API/service、实际 Pi SDK 和现有生产 entrypoint；假 transport/stream 只替代网络模型。禁止绕过有问题的链路直接调下游函数后宣布修复。
- 用现有失败实现先出现可说明的失败，再执行修复后期望；每条记录代码版本、SDK/算法版本、命令、输出和限制。
- 撤销、过期、错角色/空间/来源、并发重试和权限下降分别测试。fake Provider 成功不代表真实生成质量；静态发现没有执行就标未执行。
- 不为文档工作重跑高成本业务检查。实施时只跑实际受影响检查，变化/失败/疑点才追加。
- A/B/D 新迁移先核对当日 heads，再在隔离库运行 upgrade；报告重复/无法解析来源而不静默处理。

## 2. A：真实记忆链

| ID | 构造与观测 | 判定 |
|---|---|---|
| V-A01 | 从实际 UI/API serializer 取得旧与新 payload，经真实 FastAPI 创建、列表、确认、忽略、Memory 列表与关键词检索；记录响应和各表数量 | 新显式来源正常闭环；旧缺来源拒绝；raw_quote 可序列化；不再提交后 500 |
| V-A02 | 真实本人 user/他人 user/本人 assistant/工具派生、RAG 来源版本、跨空间、scope/sensitivity 变化；候选创建后撤销/退出；删除聊天；legacy 迁移 | 原始 user 独立快照可用，派生来源不洗权；列表/详情/检索均不返受限原文/摘要；无 FK/CHECK 500；legacy 只有验证后恢复 |
| V-A03 | Memory/RAG 四组合；部署 hard-off、DB override、功能状态请求 404/网络失败 | Memory off 无有效保存动作；RAG 独立可用；状态请求失败显示失败，不当成功/关闭 |

V-A01 另覆盖：同操作 key 同内容、同 key 异内容、提交后响应丢失、并发创建/确认、确认异参、幂等重放时失权。验证数据库结果是 0/1 条的实际状态，不能只断言 HTTP 正常。

A 的检索闭环使用现有能匹配的完整词组，避免 A 的契约修复被 B 尚未完成的中文召回混淆。B 随后负责日常问句指标。

## 3. B：检索、预算与引用

检索样本和正反例见 [retrieval-cases.md](retrieval-cases.md)，核心集与扩展集分开。

| ID | 构造与观测 | 判定 |
|---|---|---|
| V-B01 | 核心中文 16 个正例、已有英文回归、扩展集；真实 search_rag/ContextBuilder 记录 source ID、rank、plan_version、扫描量 | 核心 Recall@5 = 100%；精确英文不退化；扩展集独立测，不硬编码答案 |
| V-B02 | 两字/单字/空白/标点/FTS 运算符/超长输入，权限前排被过滤、短词后备、长中英跨块、RAG 包装预算 | 无越权/SQL 语义注入；有界补足与扫描；新检索仅活动版本；预算排除有原因；未声称完整请求已安全 |
| V-B03 | 真实 context API→client parser→worker/events→后端持久化→SSE/history/引用补取 | 当前 attempt/build 的真实使用来源一致；未使用/伪造/错绑定无认证；重新 included 的同 handle 跨轮可用；撤权后读取遮罩 |
| V-B04 | role/text/web_citations 加长 Unicode 引用，JSON 转义与 UTF-8 边界；正文已占满；来源补取失败 | 每个公开事件 ≤16 KiB，正文不被引用挤掉；完整引用经有限元数据存储与授权补取可得；重试/错误可见 |

V-B03 必须覆盖：
1. 原请求与服务端认证后 payload 不同，首次提交丢响应后同 fingerprint 返回原提交；异 fingerprint 同 seq 冲突。
2. 首次提交后来源撤销，合法重试不改写原记录；SSE、Last-Event-ID 重放、历史刷新和引用详情均按当前读者授权。
3. 同 attempt 重复取 context 复用 build；来源变化返回明确失效。新的 attempt 有新 build，过期 attempt/token 不能借用。
4. 签名 token 缺 attempt 的升级路径不默补当前值；现有取消、租约、终态和身份门禁不退化。
5. citations 缺失的旧消息正常；unavailable_citation_count 不需要伪造缺字段来源对象；补取结果与历史相同。

## 4. C：实际 Pi 复现与回归

| ID | 构造与观测 | 判定 |
|---|---|---|
| V-C01 | 实际 buildRunSession + 锁定 SDK，空/多条/连续 user、相同文本不同 ID、重复同 ID、最新 user；在初始化后观察 manager/state | 合法旧历史在同一 manager/state，按 ID 去重；当前 user/RAG 仅 prompt 一次；恢复无模型 turn/用户新增事件 |
| V-C02 | 旧 user 唯一事实 + 旧 assistant + 当前问题；streamOverride 捕获正常/摘要请求；分别手动 compact 与真实自动阈值触发 | 摘要请求可见旧事实，压缩后继续调用看见该事实摘要；真实自动事件被观察，不能把手动测试算自动 |
| V-C03 | Run/进程重建、取消、lease loss、不同 Provider/model、超大当前输入/历史 | 恢复文字及 Provider 绑定不退化；不独立重放旧工具/RAG blocks/thinking；超限清楚失败，不静默截历史 |

网络 fetch 默认阻断，全部内容合成。已有探针只证明手动压缩问题，不证明生产自动发生频率。历史 Assistant 正文可能含以前回答中的事实，仍按既有会话合同恢复；C 不实现历史文字撤销或物理擦除。

完整窗口/摘要策略另由 V-E03 验证，不给 C 附加“任意长输入都可成功”的虚假目标。

## 5. D：索引、迁移与维护

| ID | 构造与观测 | 判定 |
|---|---|---|
| V-D01 | 同 revision active/tombstone、deleted/revoked/expired、legacy、root source 失效、同源重复 index/rebuild、两个执行者 | 失效不复活；同版本 ID 稳定；动态失权不全局删；状态/唯一约束有效 |
| V-D02 | 关闭时确认、晚开启、仅 RAG 部署；有限全轮扫描、单条坏数据、重启、失败退避、过期 lease 与旧策略写回 | 真实维护入口补齐；游标按扫描 ID 原子推进；坏记录不堵塞，过期执行不能回退/换回活动版本 |
| V-D03 | 旧 schema 数据迁移、重复 source 组、revision 镜像冲突、切分版本 staging/切换/回滚、FTS 物理 repair、来源验证后恢复 | 危险重复阻断而不自动删；新检索只活动版本、RAGHit 为真实版本；旧引用/保存依赖精确原片段；FTS 不改业务状态 |
| V-D04 | effective flags 全组合、维护过程中关闭、admin/家庭状态读取 | 保留 deployment AND DB；PUT 小事务；无 Steward/模型调用；返回安全计数/状态，无原文/密钥 |

竞争测试用受控同步点，而非随机 sleep 期待碰撞：来源验证后撤销、物化前退出空间、旧 worker 暂停后新 worker 提交、最后扫描 ID 与失败登记之间中断。后台物化合法性与 actor can_read 分别断言。存量处理一旦需不可逆合并/删除，先保留报告，不能在测试或 migration 中默许生产清理。

## 6. E：后续能力协议

| ID | 输入与方法 | 交付判定 |
|---|---|---|
| V-E01 | 同冻结集比较一次预取/主动工具、词法/混合/rerank、确定性/模型排序；事前冻结成本上限 | Recall@5/MRR、来源覆盖、错误命中、p50/p95、调用/费用/资源和权限负例；未测写未测 |
| V-E02 | 显式/自动候选含否定更正、临时信息、派生来源、重复；导入失败/换版/撤销；编辑/scope/保留 | 所有入口保留来源依赖与确认，物理保留选择有记录；不自动写新知识/扩大范围 |
| V-E03 | 两种窗口、长中文、超大新输入/工具结果、摘要跨 Run/版本/来源失效与尾部去重 | 全请求预算各组成均计入、压缩后复核；摘要无隐形持久来源；方案未选则延期 |
| V-E04 | Steward 六类 shared RAG 场景；四列能力状态表；原任务接口与开关所有权 | 不读 private/session、不改确定性事实；交接到原任务，未把旧零调用当当前事实 |
| V-E05 | fake transport 的多 job 候选→后续 core→Suggestion→dismiss→相关新证据→再投影；无关/无新证据对照；冻结时钟 | fixture 事前标注 expected_support_fact_ids；记录每阶段 ID/digest/hash/status/是否调用，区分未接线/去重/未投影；UX 到期重现需另决定 |
| V-E06 | 同空间账户的 card/term/correction 与 family_recommendation 冷却，实际 rebuild 两次，同事件集对照 | 非本键族完全保留，本键族结果一致；行为开关关/开分测，不把最小修复扩为新增水位系统 |

E 的完整方案在 [设计](../../09-13-agent-memory-capability-plan/design.md)。MR-23/26 的实际结果目前未产生；保留条件性/待复现标签。

## 7. 集成与最小充分检查

V-I01：A→B→C→D 整体数据链以受控 fake Provider 联调，UI 创建/确认→索引维护→中文提问→Pi 压缩→可核验引用→刷新/撤销，分别检查授权状态和持久数量。顺序指验收数据流，不改变实施 A→C→B→D。

实施时的现有命令入口（具体 pytest 集按最终改动选择，并包含新增回归）：

```bash
# backend 工作目录
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q tests/test_memory_rag_models.py tests/test_memory_rag_service.py tests/test_platform_features.py tests/test_internal_agent_api.py tests/test_policy_guard.py

# agent 工作目录
npm run lint
npm run type-check
npm test
npm run build

# frontend 工作目录
npm run lint
npm run type-check
npm test -- src/stores/__tests__/memory.spec.ts src/stores/__tests__/agent.spec.ts src/components/memory/__tests__/MemoryManager.spec.ts
npm run build

# 主检出/集成环境
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-memory-rag-smoke.json
```

B 另覆盖现有 test_agent_tokens.py、test_agent_events.py、test_agent_sse.py、真实 context/schema 合同和新引用 API；D 覆盖 test_maintenance.py；E 复现复用 test_steward_assist.py/test_steward_suggestions.py/test_family_recommendations.py 等生产造数方式。源文件不是 context manifest 输入，实施者按代码索引自主阅读。

smoke 退出码 2 表示环境阻塞，不算通过。管理员前端只在实际修改时执行它的全套检查；全后端/前端扩展测试按新变更风险决定。真实线上有效开关、真实模型摘要/答案质量与生产延迟需另测，任何本地通过不能代替。

## 8. 规划校验

V-P01：六个 task.py validate、十二个非空 manifests、26 个 MR 全覆盖、PRD/design/implement 完整、Markdown 本地链接有效、无模板占位、父子关系/planning 状态/无实施分支与 worktree、上下文预算。结果记录在 planning-validation，不勾选任何业务修复 AC。
