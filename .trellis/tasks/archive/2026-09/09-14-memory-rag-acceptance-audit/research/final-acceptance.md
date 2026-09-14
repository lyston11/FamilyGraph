# 双 Agent 记忆与 RAG 最终验收

2026-09-14：验收、main 合并、归档和清理均已完成。原 B-I01～10、D-I01～10 共 20 组问题已闭合；D 独立复核新发现的一项合法索引恢复回归也已修复。父 AC-01～10、修复 F-01～12 均有下列证据。此结论覆盖本期 A/B/C/D 修复与 E 研究交付，未把 E 的延期能力算作生产实现。

业务修复提交为 `aebee83c3feda56c2df47e61a13919bf037829f9`，含验收制品的累计提交为 `dd8157c`。该候选已包含 A `d1f43a5`、C `8e91c42`、B `b6688f8` / `bc76e95`、E `67e9316` 与 main `461d691`。最终审查材料提交 `a83b5d1` 已随父候选 fast-forward 合入 main 并 push；随后七个任务逐项归档并清理 worktree/本地分支，归档提交已 push 至 `9583011`。完整执行与清理回执见 [集成收尾记录](integration/closure.md) 和 [父执行记录](../../09-13-agent-memory-rag-remediation/research/execution.md)。

## 验收证据

| 检查 | 实际结果 | 制品 / 解释 |
|---|---|---|
| 后端完整检查 | **1352 passed / 3 skipped**；Ruff、format、mypy 通过 | [检查报告](final/backend-check.json)、[原日志](final/logs/backend-check.log.gz)、[JUnit](final/logs/backend-junit.xml.gz)；正确 checkout 导入，检查前后源码 hash 不变 |
| 累计前端 | **660 passed / 69 files**；lint/type-check/build 通过 | [前端记录](final/frontend-check.json)、[原日志](final/logs/frontend-check.log.gz)；包含 main 后续前端改动，没有拿 B 的旧625条计数代替 |
| Agent / Pi | B 检查点 **118 tests** 与 lint/type-check/build 通过；D 再 build、真实 Pi smoke 通过 | [B验收](../../09-13-rag-retrieval-citations/research/acceptance-check.md)；[代码绑定](final/code-checkpoint.json)证明 agent/shared 对 bc76e95 无差异，保留 C overflow→summary→retry 成功结算 |
| 独立 D 语义复验 | **15 passed / 1 deselected** | [D复核](../../09-13-rag-index-lifecycle/research/acceptance-check.md)、[独立绿测](final/independent/final-review.log.gz)；排除的是旧 stage 无恢复合同的首轮探索断言，新 stage 保持跳过的正对照通过 |
| 迁移独立 oracle v2 | **8/8** | [结果](final/migration.json)；实际 Alembic FK OFF/ON × 合法非空、重复来源、revision镜像冲突、旧0045降级；保全真实保存依赖、chunk ID/text/version、FTS rowid 与 Memory/candidate 原行 |
| 真 listener / SidecarWorker / Pi / 维护 | **95/95，exit 0** | [结果](final/agent-memory-smoke.json)；三 listener、RAG-only 晚开启、真实维护、两轮历史与引用、负例、撤权/丢响应/重连/补取。仅模型 stream 合成，无真实 Provider 推理 |
| 家庭 / 管理员 API smoke | **56/56，exit 0** | [结果](final/api-smoke.json)；在最后恢复补修前执行，该补修后的全量后端与95项链覆盖受影响入口 |
| 冻结检索 | 中文 **16/16**，英文 **2/2**；扩展 **7/10、MRR 0.65** | [结果](final/retrieval.json)；fixture SHA 不变，保留扩展集局限，不把词法改进说成通用语义能力 |
| main 合并后复验 | **95/95，exit 0**；Agent build 通过，迁移单头0047 | [实际main结果](integration/main-agent-memory-smoke.json)、[源码与制品核对](integration/main-code-integrity.json)；631个源码文件与累计受验版本一致，未重复无变化的完整套件 |

受验代码由 [提交与包树绑定](final/code-checkpoint.json) 固定；部分命令报告中的 `candidate_commit=c253cb6` 是测试时的旧 HEAD，工作区修复由报告中的 source hash 标识。主线程已逐项对照 `git show aebee83:<path>`，与最终提交相同。最新 24 份制品见 [哈希清单](final/artifacts.json)。原始 30 份冻结制品及6份解压原日志也已复核，均与 [历史清单](artifacts.json) 一致。[最终独立核验](final-check.md) 已通过，未发现剩余实质阻断项。

后端3项跳过是已有管理员 break-glass 延期测试；4条 datetime adapter 警告来自 Python3.12/SQLite。前端全套出现一次 jsdom ResizeObserver 清理 stderr，命令和全部断言仍通过；随后8个 memory/agent 文件80项与 main 基线618项均通过，未复现该输出。此项保留原日志与范围说明，未声称已经定位修复，也未为此改动无关前端。

## 20组原问题闭合

原始事实、严重性与 MR 映射见 [问题台账](findings.md)，以下只记录最终实现及反例的去向，不改写历史红测。

| 发现 | 最终行为与回归证据 |
|---|---|
| B-I01 | 签名 ExecutionIdentity 贯穿实际 writer/admission，Run/Job 双 attempt、lease、scope 和状态重验；真实鉴权后换租反例及 provider/tool 准入回归 |
| B-I02 | ExactChunkRef 固定 chunk/revision/version/hash；删除、改文、改版本、改revision 不获认证，合法历史块可读 |
| B-I03 | 服务端独占可信引用保留字段；伪造 public citations 不流出，SSE/history/fallback 使用统一当前读者投影 |
| B-I04 | internal history 仅发送允许的正文，撤权后不携带旧结构化来源定位，C 文字恢复保持 |
| B-I05 | 同 attempt 并发 context 重取唯一，关闭/来源/策略失效单调保留；事件整批 rollback 不撤回已观察的失效状态；合法空构建保持为空 |
| B-I06 | 实际 ContextBuilder 和 sidecar 包装复用 UTF-8 估算及冻结包络，整块选择包含标记/句柄/指令的真实子预算 |
| B-I07 | 授权后不足继续有界补足，FTS/LIKE 共用200个返回候选预算；LIKE 的 !/%/_ 保持字面含义 |
| B-I08 | 仅有限同会话历史中唯一明确锚点用于追问；不同前文产生不同锚点，歧义/缺失/超窗降级 |
| B-I09 | fallback 绑定 run所属session、assistant role和服务端事件key，跨会话同key不串取 |
| B-I10 | 私有 context_reference(build/attempt/used_handles) 贯通协议；原请求v2指纹在认证前生成，精确16384/+1边界、撤权重试和真实wire验证 |
| D-I01 | 数据库规范来源唯一键与revision镜像约束；两个真实Session竞争一致，重复存量/镜像冲突迁移无损拒绝 |
| D-I02 | 完整输入摘要与raw_quote分离，已有chunk不可变；冲突或错误staging整批回滚，不以删除/覆盖改变旧引用 |
| D-I03 | 不可变 lease 全字段条件写，旧Session/owner/attempt/expiry/round/policy/target/cursor/watermark不能回写 |
| D-I04 | 真实tick先提交core，再用独立RAG Session；最终拒绝与失租整批回滚，已完成Steward core保留 |
| D-I05 | stage与batch共同遵守真实lease、最终开关与新鲜来源；writer前撤销及最终拒绝无部分物化 |
| D-I06 | 真v1/v2算法注册，未知目标拒绝；新搜索只读实际活动指针，普通补建不降级，旧ExactChunkRef/保存依赖仍精确读取 |
| D-I07 | 完整预期块与FTS验证；可信摘要下修补缺项，NULL摘要缺块不能补签，完整旧集合可建立证据 |
| D-I08 | 0045 downgrade首项破坏动作前拒绝不兼容库；0047原地升级保全FK依赖，存在正文证据/未来策略则拒绝降级 |
| D-I09 | 两类扫描各自固定有限水位，持续新增不会饿死恢复的低ID；全轮扫描遵守失败退避 |
| D-I10 | FTS repair复用全局来源合法性，不修改业务状态；个人暂时失权不生成全局tombstone |

B 的持久回归位于 `test_rag_acceptance_contract.py`、`test_rag_acceptance_bindings.py`、`test_rag_query_context.py` 等；D 位于 `test_rag_lifecycle_acceptance.py`、`test_rag_lifecycle_migrations.py` 和 `test_rag_index_lifecycle.py`。详细源码位置及测试说明见各自 [B](../../09-13-rag-retrieval-citations/research/acceptance-check.md) / [D](../../09-13-rag-index-lifecycle/research/acceptance-check.md) 检查报告。

独立 D 检查另发现新修复过度拒绝 `invalidated/index_superseded` 的合法 Memory。已增加严格的来源/metadata/完整内容验证后恢复路径，保留活动指针、原ID和正文，最后拒绝与FTS一起回滚；24条持久回归与独立原红测已通过。授权文档 ingest、stage、来源 tombstone/未知原因仍不获得此例外。这一附加回归单独列出，不篡改原20组问题计数。

## 父任务与能力边界

父 AC-01～10 和 F-01～12 的逐项判定见 [验收矩阵](acceptance-matrix.md)。MR-01～26、E的12项优化及唯一后续所有者保持在 [覆盖与路线](coverage-and-roadmap.md)。E仅按设计、实证和采用/延期条件验收；`09-13-steward-memory-evidence-projections`（MR-23/26）保持 planning，未实施、未归档。

本轮没有调用真实 Provider、操作生产库或线上开关，未做不可逆删重、物理擦除或部署。来源无法证实完整输入时保留并报告，不承诺自动恢复所有历史数据。未验证生产延迟、模型答案忠实度或跨Run持久摘要；扩展检索仍有3/10未命中。`system-admin-frontend`没有改动，未重复其全套；相关管理员API由真实smoke验证。

## 复现与归档后读取

运行正式测试应从目标 checkout 的 backend 开始，设置 `PYTHONPATH=tests:.` 并核对 `app.__file__`。三份独立 D probe 在 `research/final/independent/`，从该目录一并加载，保留 `-k 'not (known_index_superseded_restore and stage)'`；原日志为gzip，解压后与哈希清单完全一致。

[迁移oracle](migration_acceptance.py) 接受 `--backend <当前checkout/backend> --report <独立JSON路径>`，从 Git `bc76e95` 抽取旧后端建立合成seed，无需旧worktree仍存在。真实端到端入口是当前仓库 `scripts/smoke/run_agent_memory_smoke.py --report <独立JSON路径>`，历史冻结 smoke 副本仅用于追溯。
