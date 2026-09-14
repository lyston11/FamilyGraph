# 规划校验记录

> 此文保存规划交付时的校验快照。之后用户已回复“执行”，子任务的当前状态以主检出 task.json 与实施记录为准；下文 planning/未实施描述仅对应校验当时。

## 范围与状态

2026-09-13，根据用户请求创建一个父任务和五个子任务，记录 26 项审计发现并生成修复/优化 PRD、设计、实施计划及验收协议。当前全为 planning；未运行 task.py start、未创建实施分支/worktree、未修改业务代码、未提交或部署。父任务仍是本会话当前任务。

审计基线为 b7bd368 及当时工作区；工作区已有其他任务改动，未纳入本轮写入。业务修复和 E 能力实验尚未执行。此前 211 pass 和探针仅作审计前证据，本规划轮不重复运行。

## 独立只读审阅与收敛

主线程基于三个只读探索/核验代理的建议，沿决定性代码抽查并修订文档。下列是规划稿的设计风险与修订，不把它们冒充已经发生的线上缺陷，不增加 MR 编号。

| 编号 | 审阅发现 | 已写入的决策 |
|---|---|---|
| PV-01 | 旧无来源手工/RAG payload 相同，自动转 manual 会洗掉来源 | 父设计/A/合同要求新 UI 显式 source，旧歧义请求拒绝 |
| PV-02 | 本人会话含 Assistant 派生内容，独立快照过宽 | agent_message 仅原始 user；未来保存派生消息需来源图 |
| PV-03 | legacy/unverified 的检索/展示策略不明确 | 原文和确认历史保留，来源验证前不检索/入 Context/返回原文摘要；本人元数据管理 |
| PV-04 | 认证后 payload 与原事件请求不同，会破坏现有直接相等重放 | 原始请求 fingerprint 与认证结果分开；同 seq 同指纹认原提交，读面独立遮罩 |
| PV-05 | 句柄不含 Run，可能合法跨轮复用；当前 context 无 attempt 绑定 | 验证有效 Run/attempt/build + included；新增 signed attempt；同 attempt context 幂等，旧 token 不补当前 attempt |
| PV-06 | 历史/SSE 原样返回持久元数据，旧 parser 丢缺字段来源 | api/agent.py 双读面重验；合法六字段 citations + unavailable_citation_count；补取与历史同投影 |
| PV-07 | 16 KiB 已满时无法再附引用 | 内部 context_reference 与公开 payload 分开；完整元数据存消息，按授权 run_id/seq 补取，正文不截断 |
| PV-08 | 多算法版本并存，FTS 可能同时搜到 staging/旧块 | 新搜索仅活动 document.index_version；旧引用/保存依赖精确原块；RAGHit 用实际版本，迁移含版本唯一键 |
| PV-09 | 读者失权、来源永久失效和未验证被混为 tombstone | 后台 materializable / 请求 can_read 分开，三类状态分别处理；解除验证隔离后可恢复投影 |
| PV-10 | 唯一约束不防旧维护执行者回退游标/活动版本 | 持久 lease attempt/轮次/策略条件更新；结果或失败登记/游标同事务，失败退避不被全轮绕过 |
| PV-11 | 发现旧数据重复但未规定迁移阻断 | 无法安全归并时阻断新唯一约束/维护启用，先独立处理报告，不静默删重 |
| PV-12 | MR-23 缺相关证据评估真值，当前候选只返回端点 | fixture 独立 expected_support_fact_ids、逐阶段 job/batch/candidate/suggestion 观测；不伪造 digest 绕路 |
| PV-13 | MR-26 最小修复被扩大为必须新增事件水位 | 改为同事件集重复回放一致、只改所属键族；增量水位延期 |
| PV-14 | “ActionCard 冷却有效”容易被读为当前线上已启用 | 审计/E 注明生产读写路径受 BEHAVIOR_PROJECTION_ENABLED 控制，本轮未查线上 |
| PV-15 | “不重放旧 RAG”可能误指历史回答中的事实也被清除 | C/合同明确只不单独重放原 RAG blocks，既有 Assistant 文字仍保留，历史政策归 E |

核验出处包括 memory_rag.py 的句柄/读取谓词、models/rag.py 的索引字段、agent_events.append_events 的直接 payload 比较、internal_agent._authorize_run/agent_tokens 的 claims、Steward 冷却开关与候选输出结构。详细规划与源码锚点分别在各 design 和 audit。

## 任务与上下文

- 通过 task.py create --parent 建立五个 child；主任务维持 planning，子任务也保持 planning。
- 通过 task.py add-context 写入 12 个非空 manifests，共 45 条真实文档/证据条目；没有把生产源码塞入注入清单。
- 通过 task.py set-meta 记录工作流 A/B/C/D/E、依赖与发现范围。依赖元数据用于说明，不声称 Trellis 父子关系自动调度。
- 现有 .trellis/spec 索引标注“历史资料（不可执行）”；本轮注入采用当前 AGENTS.md、本任务合同/审计/验收及既有能力任务 PRD，避免把历史规范当成现行规则。

## 结构校验结果

实际执行结果：全部通过，无结构错误或上下文截断。

| 检查 | 实测结果 |
|---|---|
| task.py validate | 六个任务全部退出 0；12 个 manifests 均通过，无占位/空清单警告 |
| 核心规划文件 | 六套 PRD/design/implement，共 18 份；加研究记录共 24 份 Markdown |
| 发现覆盖 | audit 与 coverage 各 26 条，MR-01～MR-26 每项恰好一次 |
| 需求/验收编号 | 父 R-01～08、AC-01～10 完整；A/B/C/D/E 分别 8/8/6/8/8 条验收 |
| 数据集/优化议题 | Q01～Q16 共 16 个核心中文正例；E-O1～12 共 12 项能力议题 |
| 本地文档链接 | 68 处目标及使用的片段锚点全部有效 |
| 上下文清单 | 12 份、45 条文档/证据引用，无生产源码条目、重复条目或模板占位 |
| 实际注入 | 调用本地 inject-subagent-context.py 的 implement/check 生成函数，共 12 组，均无截断或仅索引降级；最大低于 67,000 bytes，上限 131,072 bytes |
| 文件上限 | 所有引用文件小于 32,768 bytes；任务核心 artifact 小于 65,536 bytes |
| 生命周期 | 五个 child 正确指向父任务；六个状态均 planning，branch/worktree_path 为空；当前会话指向父任务 |

执行方式：依次运行 task.py validate 六个明确任务目录；单次只读 Python 检查 JSON/JSONL、Markdown 链接、编号集合和本地注入器输出。记录文本完成后只重跑受其变化影响的文档结构/注入检查，不重复业务测试。此结果只验收规划交付，未勾选任何业务修复 AC。

## 剩余限制与下一步

规划可整体审阅，后续按 A→C→B→D 执行；E 只读设计可并行，功能选型/线上实验不自动开启。MR-23/25/26 仍需要对应实际复现，Pi 真实自动触发回归仍需实施，线上功能状态/生成质量/延迟没有本轮证据。新迁移、完整业务测试、真实合同 smoke 及物理保留策略均按相应 PRD 执行或决定后回填。
