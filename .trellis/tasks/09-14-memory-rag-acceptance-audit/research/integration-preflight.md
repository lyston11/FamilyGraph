# 累计验收与收尾核对

2026-09-14 续接用户已授权的审查、修复、验收、提交、串行合并、归档和清理。本文是执行检查点，原 bd899b9 审计结果和制品哈希不变。

## 当前累计版本

- B 已完成并 push：`b6688f8`（执行身份与精确证据）及 `bc76e95`（真实 listener 验收与末轮修复记录）。主线程检查见 [B 验收记录](../../09-13-rag-retrieval-citations/research/acceptance-check.md)。backend 1201 passed / 3 skipped，agent 118、frontend 625；真实 listener/Pi smoke 95/95。它们是 B 检查点，不代替 D 之后的累计验证。
- 父候选 `b1d4dab` 已包含 A、C `8e91c42`、B 和 E 研究提交。
- D 从父候选 fast-forward，随后纳入最新 `origin/main` 的文档提交 `461d691`，以 `c253cb6` 固定本轮执行材料并 push。主分支尚未收到本轮业务集成。
- 本轮核对远端发现 C 仍在 `470b362`、A 尚无对应远端分支；已实际 push A `d1f43a5` 和 C `8e91c42` 并设置 upstream。后续清理以真实 refs/合并检查为准，不沿用“全部已 push”的旧汇报。
- 实施前实际 Alembic head 为 `0046_context_execution_contract`，单头。D 后续编号由实施者从该链向前推进，新增约束不删除旧数据。
- D-I01～10 仍在修复，F-07～11 和父 AC-06 不能提前标通过；详细补充断言见 [D 执行前核对](d-execution-preflight.md)。

## 最终检查对象

| 对象 | 实际验证入口与判定 |
|---|---|
| 后端累计版本 | 明确 `app.__file__` 来自目标 checkout；全量 Ruff、format、mypy、pytest，包含 A 来源、B 引用、D 多 Session 与 tick 回滚 |
| 迁移 | 实际 Alembic 连接 FK ON/OFF；合法非空、重复 canonical、revision 镜像冲突、legacy/tombstone、真实保存的旧版本依赖。拒绝路径比较首项 DDL 前后的 schema、chunk ID/text/version、FTS 和 Memory 来源证据 |
| Agent / frontend | 在累计版本执行 lint、type-check、tests、build，保留 C 同一 Pi SessionManager 的恢复、压缩与成功终态 |
| 家庭/管理员 API | `scripts/frontend-api-smoke.sh --report <隔离报告>`；脚本创建隔离数据和端口，退出码 2 记环境阻塞 |
| 真正维护、引用与读取出口 | `scripts/smoke/run_agent_memory_smoke.py` 已含关闭时保存、RAG-only 平台晚开启后的循环补建、真实 listener/SDK 两轮与负例；累计候选重跑。仅模型 stream 为合成，不表示真实 Provider 质量 |
| 冻结检索 | B 当前可运行的 `research/test_retrieval_probe.py`，fixture 字节不变；核心 16、英文 2、扩展 10 分组独立报告，保留历史扩展 7/10 与 MRR 0.65 |
| 原始证据 | 校验本任务 `artifacts.json` 各制品及 gzip 原始输出哈希；不重写历史红测或为适配新接口改动冻结探针 |

续接预检已校验 30 份冻结制品和 6 份解压后的原始日志，全部匹配。八个本轮/后续任务目录的 220 个 Markdown 链接中，218 个在累计 worktree 可解析；另两个指向仅在主检出存在的既有管理员开关规划，最终整合/归档时重定位该链接，不接管该任务文件。

主线程另写 [迁移核验工具](migration_acceptance.py)，从 Git `bc76e95` 抽取旧 backend，通过真实 propose/confirm 保存 RAG 片段依赖，再由旧 stage 生成保留 v2 片段的合成历史版本；不依赖可能被清理的 worktree，不拿未保存的临时句柄代替 Memory。种子还包含 unverified、revoked 和未知 tombstone。SQLite backup 生成独立输入，后续不访问开发/生产库。

该工具在 B 版本上执行 8 个场景得到 [基线结果](acceptance-migration-baseline.json)：FK OFF/ON 的正常读保全两个正例通过；两种模式下的重复来源、镜像冲突和旧 downgrade 共六项失败。旧 `upgrade head` 尚无新的唯一性/镜像预检，因此不会拒绝脏输入；旧 downgrade 则实际改变 schema、迁移版本并删除片段。D 定版后对同一 seed 重新执行，新的输出另存，不覆盖该基线。初次种子准备仅因导入到根 `conftest` shim 而停止，修正为显式 `tests/conftest` 并断言模块位置后才成功；这项 harness 修正不计产品反例。

独立核验者随后指出 oracle 的三处实质漏验：任意异常可能冒充预检拒绝；0046→0045 准备阶段未比较来源行；SQL 观察未覆盖所有写入口。主线程逐项修正，并加入真实唯一/镜像约束的写入探测、FTS rowid、FK 迁移期间及连接归还时采样。只有精确的 `UPDATE rag_documents SET id = id WHERE 0` 且 rowcount=0 的取锁语句被单列允许。v2 [加强后基线](acceptance-migration-baseline-v2.json) 为 0/8：两个原正例现在也要求新增约束和 0047 head，旧 B 因缺失这些能力按预期失败；保存依赖的种子正对照与降级准备阶段保全仍通过。旧 v1 结果保留其工具版本含义，最终使用 v2 验收。

未改 `system-admin-frontend`，不因本轮后端修复无故扩大管理员前端全套。未部署、未调用真实 Provider、未操作生产库或线上开关。

## 集成和归档约束

主检出有其他任务的既有修改。本轮续接再备份 159 个 dirty/untracked 路径至本地私有临时目录 `familygraph-acceptance-resume-ykwfjxrj`，包含哈希清单和 binary diff；该目录不提交。集成前重新比较，只有本任务已提交的镜像允许暂存到备份后移开以消除 merge 覆盖冲突，其他改动不纳入提交。

通过后由本会话作为唯一串行集成通道完成：

1. D 验收后合入父累计分支；Audit 从该累计提交建立自己的分支/worktree，固化最终 F-01～12、父 AC-01～10 与证据。
2. 主检出读取最新远端，合入已验收的累计分支；如基线又变化，只对新增影响补查，不能改写 main 或其他分支历史。
3. 归档 A、C、B、D、E 研究、Audit 和父任务。E 只按研究交付验收；`09-13-steward-memory-evidence-projections` 保持 planning，归档 E 后成为活跃根任务。
4. 归档后修正维护文档/JSONL 到新路径；历史哈希制品保持原字节，另以归档索引说明原路径。P2 的父/E 证据路径及既有 kinship 交接行必须保留。
5. 每个已合并且干净的任务立即删除 worktree，再用 `git branch -d` 删除本地分支；不使用 force，不碰 snapshot/Orca 或其他任务。保留远端分支作为已提交证据。
6. 写会话 journal，核对主检出无新增本任务业务脏文件、远端同步和原无关改动保全。

本检查点不宣称最终验收、main 合并或归档已经完成；完成结果另写累计验收记录。
