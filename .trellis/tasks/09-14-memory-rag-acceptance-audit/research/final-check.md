# Audit 最终独立核验（2026-09-14）

**判定：最终交付内容与冻结验收证据一致，本轮审阅范围无剩余实质阻断项。** 业务源码基线为 `aebee83c3feda56c2df47e61a13919bf037829f9`，含制品的累计候选为 `dd8157c`。本记录不代表 main 合并、任务归档或 worktree/分支清理已经完成；这些步骤由主线程在[父执行记录](../../09-13-agent-memory-rag-remediation/research/execution.md)留证。

本轮依照 trellis-check 清单复核任务合同、最终材料、原始结果与代码绑定；B 沿用[既有独立验收](../../09-13-rag-retrieval-citations/research/acceptance-check.md)，D 沿用本核验者已完成的[独立审查及恢复补修复核](../../09-13-rag-index-lifecycle/research/acceptance-check.md)。仅新增本文件，未修改业务、测试或冻结制品。

## Findings (fixed)

- File：`research/coverage-and-roadmap.md:3`、`:28`。
  Issue：原表仍以“B-I07授权后补足仍失败”“仍需修复后累计验收”等文字表达旧状态，缺少当前处置，易被误读为最终验收尚未通过。
  Fix：主线程补齐覆盖 MR-01～26 的最终处置表，并将旧表明确标为 `bd899b9` 历史。已复读确认，原问题编号与历史事实保留。
- File：`research/validation.md:3`、`:8`。
  Issue：C 的“未push/合入累计代码或main”原检查点陈述缺少显著的历史边界，与最终交付状态冲突。
  Fix：主线程增加顶层说明，明确 C `8e91c42` 已 push 并累计，B/D 原20组与追加恢复回归已闭合，最新结论指向最终验收。旧命令、红测和30份制品保持原样。
- File：`.trellis/HANDOFF.md:54`、`:80`；`docs/ARCHITECTURE.md:134`、`:161`。
  Issue：主线程自查发现入口文档仍描述 B/D 未完成、C 未累计和旧迁移头。
  Fix：主线程更新为累计验收、20组闭合、C 已 push/累计及 `0047_rag_lifecycle_integrity` 单头；本核验者逐段复核通过。文档保留生产部署、E/P2 延期与其他任务独立状态，未预先宣称完成主线集成。

## Findings (not fixed)

- 前端完整套件原日志出现一次 `TypeError: global.removeEventListener is not a function`，位置在 jsdom 下的 `@juggle/resize-observer` 清理流程。全部断言和命令退出码通过；后续相关80项及 main 基线618项未复现。原因未定位，未按已修复报告，未扩大范围修改无关前端。
- 冻结检索扩展集仍有 EX01、EX03、EX06 未命中；E 的全请求预算仅为18项合成原型断言，MR-23/MR-26 保持独立 P2 `planning`，生产修复未实施。它们是已明示的后续范围，不能据本轮通过改写为已交付能力。
- 主线合并、归档、清理仍待主线程完成。[集成预检](integration-preflight.md)登记的两处主检出管理员开关规划链接，也由主线程在归档时重定位；本轮未接管其任务或将链接迁移记作已完成。

## Verification

本轮新执行的是只读制品哈希/解压校验、JUnit/JSON/原日志统计、源码包树绑定和文档集合/链接检查。应用 lint、类型检查、全套测试、smoke 和迁移矩阵未重复执行；下列结果来自已执行且与最终源码绑定的证据。此前 D 的独立探针、lint/format 和4模块 type-check 由本核验者实际执行并复核通过。

- Lint：**pass（已执行证据复核）**。后端 Ruff/format、前端 lint、Agent 检查点 lint 均通过；后端 format 记录357个文件。
- TypeCheck：**pass（已执行证据复核）**。后端 mypy 记录193个文件通过；前端、Agent 检查点 type-check 通过。
- Tests：**pass（已执行证据复核）**，具体计数与时点如下。

| 范围 | 独立核对结果 |
| --- | --- |
| 后端完整检查 | JUnit 总数1355，0 failure / 0 error / 3 skipped，即1352 passed；5条检查命令 exit 0。3项跳过均为既有延期的 admin break-glass 用例，24项 `index_superseded` 持久回归全部通过。 |
| 前端 | 原日志69个文件、660 passed；lint/type-check/test/build 均 exit 0。上述一次 stderr 如实保留。 |
| Agent / Pi | B 独立验收118 passed、lint/type-check/build 通过；最终 agent/shared 与该检查点包树完全相同。未将此项表述为本轮重跑118项。 |
| D 独立复核 | 最终15 passed / 1 deselected；原 ensure/batch 恢复红测转绿。排除项是已说明的 stage 恢复探索；另有 stage 保持 skip 的正向验证。 |
| 真实 listener / SidecarWorker / Pi / 维护 | JSON逐项95/95通过，failed=0；模型 stream 为合成，外部网络尝试0。 |
| 家庭/管理员 API smoke | JSON逐项56/56通过，failed=0；执行于最后恢复补修之前，补修后的后端全套和95项真实链覆盖受影响路径。 |
| 迁移 oracle v2 | FK OFF/ON × 合法非空、重复来源、revision镜像冲突、旧降级，8/8全部检查为真；实际连接 PRAGMA、保存依赖、唯一/镜像约束和拒绝前零变更证据一致。 |
| 冻结检索 | 按逐例结果重算：中文16/16、英文2/2，MRR均1；扩展7/10、MRR 0.65，漏项EX01/EX03/EX06。 |

证据完整性和最终代码绑定：

- [历史清单](artifacts.json)：30/30文件 SHA-256 匹配，6/6 gzip 解压原文哈希匹配；[最终清单](final/artifacts.json)：24/24文件匹配，13/13 gzip 解压原文哈希匹配。两组清单各自无重复路径，历史红测未被最终结果覆盖。
- [代码检查点](final/code-checkpoint.json)：读取并验证 Git 对象/index，按当前文件字节及可执行位重算5个包树，均与 `aebee83` 和检查点一致；共631个 tracked 文件（backend 366、agent 40、frontend 208、shared 1、scripts 16）。agent/shared 同时与 B `bc76e95` 相同，frontend 与测试时 `c253cb6` 包树相同。
- 后端8个检查前后源码哈希、3份迁移源码、迁移 oracle 及检索源码哈希均与最终文件匹配。旧报告中的 `candidate_commit=c253cb6` 是测试时 HEAD 标签；实际受验修复由源码哈希和最终提交绑定，不能误读为在未经修复的旧源码上通过。
- 检索 fixture 仍为 `92f0bed8c438a0bfde6672d71e6bb47db0f039c4cd1ac07fc490a7b3ac6c675a`；迁移 oracle 为 `2d2b873cb1640fdf4faa802b39b8389ad2869461b084a89a867c727bf26744c0`。

覆盖与文档：原台账与最终闭合表均恰含 B-I01～10、D-I01～10；追加恢复回归单列。验收矩阵含 AC-01～10、F-01～12，当前处置含 MR-01～26，路线含 E-O1～12。E 研究核验、能力登记和 P2 元数据保持研究/规划边界；D 的索引生命周期规范已在前次独立复核确认同步。Audit 9份及父任务4份当前材料共102个本地文件链接均可解析，此结果不替代归档后的链接核查。

未验证真实 Provider 的答案忠实度、生产规模延迟/成本、线上开关、生产迁移或部署；没有据合成模型和临时数据库结果作这些推断。`system-admin-frontend` 没有本期改动，沿用未重跑其全套的既有范围说明。
