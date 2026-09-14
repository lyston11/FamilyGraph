# Implement：Steward 记忆证据后续修复

本包为 E 研究的明确后续所有者。2026-09-14 用户在查看任务后回复“执行”，任务已 start；实施范围为 MR-26 → MR-23 串行修复。新证据只作内部版本核验，不新增关系提醒/待办，不运行线上模型或改生产数据。

## 实施前

- [x] 审阅 SP-R1～6，冻结共同 biological_parent 证书及仅内部版本、保留驳回、零新待办策略（见 design §2～3）。
- [x] 核对当前 Steward 能力和平台开关任务的文件所有权，读取最新代码和迁移 heads。
- [x] 读取 [称谓交接](../09-13-steward-kinship-capability-closure/research/task-alignment.md)，消费已集成 A/B 及后续质量修复；保留 viewer/非待办/自动显示语义及个人恢复反馈。
- [x] 获准实施后在主检出 task.py start，进入记录的专用分支/worktree；不改主检出业务代码。

启动基线 `0db88c2`；task branch `feat/09-13-steward-memory-evidence-projections`，worktree `../fg-09-13-steward-memory-evidence-projections`。主检出既有脏文件/index 已单独取哈希快照保全。按用户要求等待后，渐进重算已在 `dee91a1` 合入 main。任务 worktree 先同步 origin/main，再 fast-forward 到包含已集成代码的本地 main；当前基线 `dee91a1`，单一迁移 head 为 `0048_steward_terminology_publication`。共有代码已释放，本包按 MR-26 → MR-23 串行实施，下一迁移采用 0049。

## 串行步骤

- [x] MR-26：把现有 4 组合、无事件、未知键族及其他账户样本转为失败回归。独立新测试首轮 4 failed / 4 passed；失败均为预期冷却丢失/外部键误删，非夹具或迁移错误。
- [x] 限定 rebuild 删除键族，复用实际事件处理器键定义；同事务重放，确认所属语义幂等。
- [x] 独立检查并提交键族修复（`cff6f8e`），不顺带接入 maintenance。
- [x] MR-23：冻结结构/相关证据版本合同及迁移；保留无法归因的旧记录。
- [x] 实现支撑证据认证、版本唯一性与投影重验；保留 batch 快照竞争栅栏。
- [x] 分离证据更新与通知副作用，落实已审阅的可见策略。
- [x] 运行真实多 job 回归和并发/撤权/重试/通知反例；通过全范围检查并更新合同，提交/集成记录随收尾补入。

## 检查

使用专用迁移临时库，全部合成数据，模型 transport 为 fake。依赖环境若链接其他 checkout，显式 `PYTHONPATH=.` 并用 `.venv/bin/python -m pytest`，先确认 `app.__file__` 来自本 worktree。

已建立本 worktree 的 backend/.venv 依赖链接，`PYTHONPATH=.` 下 `app.__file__` 指向本任务 backend/app/__init__.py。独立 MR-26 测试仅写新文件 `backend/tests/test_steward_behavior_rebuild.py`，没有与渐进重算在途合并共写服务或迁移。其最新设计已将旧 core 投影改为 `steward_delivery` 发布后交付，本包按 design §3 适配，不重新引入长事务。

相关 pytest 覆盖 `test_steward_assist.py`、`test_steward_suggestions.py`、`test_family_recommendations.py`、实际行为投影回归和新增证据版本测试；静态检查执行 ruff / format / mypy。变化涉及迁移时先在隔离旧数据样本上 upgrade。

完成标准为 SP-AC1～6 有证据；未实现项保持未完成。主分支集成、部署和破坏性清理需按届时授权范围处理，不能用规划记录代替验证。

## MR-26 检查完成

`steward.py` 已将重建所属前缀与全局写白名单分开，两个删除分支采用大小写敏感的 `substr` 等值谓词，三个事件处理器共用前缀定义。真实推荐忽略链、无事件与近似键、跨账号/空间及重复重放共 12 项相关测试通过（1.86 秒）；Ruff check/format 和 mypy 204 个源文件通过。独立 `trellis-check` 未发现问题，未修改代码；见 research/validation.md。新合同记录在 `.trellis/spec/backend/steward-behavior-rebuild.md`。

## MR-23 集成准备

MR-26 已独立提交 `cff6f8e`，并创建本任务本地 backup 分支。随后合入 main 的渐进重算归档与 journal（无新的运行时代码变化）。真实发布/交付/fake assist 的夹具、捕获版本 ID 与 lease 反例见 [集成摘要](research/mr23-integration-summary.md)。

0049 除自身拒绝丢弃证据外，还必须在首个 DDL 前复用祖先拒绝预检。允许对 0048 helper 增加可选 planned 参数，默认保持原逻辑；0049 传入从自身头计算的计划，不重复历史 SQL，也不新增计划缓存。真实验证覆盖 `-1` 到 0048、当前 merge 图下 `-2` 无 DDL 拒绝、绝对深降级及已有 RAG/Memory schema/head 保全断言。相应 0048 publication 测试的当前 head 改为动态单头；历史父 revision 与拒绝断言保持原意。

## MR-23 实施与定向验收

归因状态、共同父母相关证书、不可变版本及后续 core 捕获 ID 的 candidate 交付已实现。两个公开投影入口共享无向端点对隔离；已有 payload/job、建议、通知、确认与私人/共享驳回保留。0049 使用原地新增列并保留祖先降级拒绝。主线程审查发现并以真实 SQLite writer 复现了入锁前时间导致的过期写回，现已在 writer 内重采样修复；失败证据见 [租约调查](research/evidence/lease-writeback-investigation.md)。

三个新测试文件加相邻 assist、建议、推测、称谓交付、迁移与渐进交付回归共 188 passed（84.19 秒）；14 个 MR-23 文件 Ruff/format 通过，mypy 205 个源文件通过。隔离迁移为单一 0049 head，FK check 为空。同候选两个 pending 版本独立交付、准备后新增版本等待下一 core、失败时核验与回执一起回滚及双执行者复用均已覆盖。

完整后端为 1594 passed / 3 既有 skipped / 4 既有 warnings（156.36 秒）。全包 Ruff 发现六个既有渐进 Steward 测试的 import 顺序问题，主线程只作机械整理，随后对应 43 项回归通过（7.42 秒）；最终 Ruff、format 394 文件和 mypy 205 个源文件均通过。完整测试后生产文件与三套新测试逐字节未变，证据 manifest 已保存。

独立最终 `trellis-check` 已直接阅读 MR-26/MR-23 全部实现、迁移和新增回归，没有遗留可操作缺陷。SP-AC1～6 均满足；详细命令、原红测和限制见 [验证记录](research/validation.md)，摘要见 [最终验收](research/final-validation-summary.md)。功能提交为 `cff6f8e`（MR-26）与 `b43602d`（MR-23）；main 已由 `6fed610` fast-forward 至 `b43602d`。Trellis 归档与任务 worktree、feat 分支及两个本地 backup 分支清理均已完成；未使用强制删除。
