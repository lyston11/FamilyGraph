# Implement：Steward 记忆证据后续修复

本包为 E 研究的明确后续所有者。2026-09-14 用户在查看任务后回复“执行”，任务已 start；实施范围为 MR-26 → MR-23 串行修复。新证据只作内部版本核验，不新增关系提醒/待办，不运行线上模型或改生产数据。

## 实施前

- [x] 审阅 SP-R1～6，冻结共同 biological_parent 证书及仅内部版本、保留驳回、零新待办策略（见 design §2～3）。
- [x] 核对当前 Steward 能力和平台开关任务的文件所有权，读取最新代码和迁移 heads。
- [x] 读取 [称谓交接](../archive/2026-09/09-13-steward-kinship-capability-closure/research/task-alignment.md)，消费已集成 A/B 及后续质量修复；保留 viewer/非待办/自动显示语义及个人恢复反馈。
- [x] 获准实施后在主检出 task.py start，进入记录的专用分支/worktree；不改主检出业务代码。

启动基线 `0db88c2`；task branch `feat/09-13-steward-memory-evidence-projections`，worktree `../fg-09-13-steward-memory-evidence-projections`。主检出既有脏文件/index 已单独取哈希快照保全。按用户要求等待后，渐进重算已在 `dee91a1` 合入 main。任务 worktree 先同步 origin/main，再 fast-forward 到包含已集成代码的本地 main；当前基线 `dee91a1`，单一迁移 head 为 `0048_steward_terminology_publication`。共有代码已释放，本包按 MR-26 → MR-23 串行实施，下一迁移采用 0049。

## 串行步骤

- [x] MR-26：把现有 4 组合、无事件、未知键族及其他账户样本转为失败回归。独立新测试首轮 4 failed / 4 passed；失败均为预期冷却丢失/外部键误删，非夹具或迁移错误。
- [x] 限定 rebuild 删除键族，复用实际事件处理器键定义；同事务重放，确认所属语义幂等。
- [ ] 独立检查并提交键族修复，不顺带接入 maintenance。
- [ ] MR-23：冻结结构/相关证据版本合同及迁移；保留无法归因的旧记录。
- [ ] 实现支撑证据认证、版本唯一性与投影重验；保留 batch 快照竞争栅栏。
- [ ] 分离证据更新与通知副作用，落实已审阅的可见策略。
- [ ] 运行真实多 job 回归和并发/撤权/重试/通知反例；通过后检查、更新合同并提交。

## 检查

使用专用迁移临时库，全部合成数据，模型 transport 为 fake。依赖环境若链接其他 checkout，显式 `PYTHONPATH=.` 并用 `.venv/bin/python -m pytest`，先确认 `app.__file__` 来自本 worktree。

已建立本 worktree 的 backend/.venv 依赖链接，`PYTHONPATH=.` 下 `app.__file__` 指向本任务 backend/app/__init__.py。独立 MR-26 测试仅写新文件 `backend/tests/test_steward_behavior_rebuild.py`，没有与渐进重算在途合并共写服务或迁移。其最新设计已将旧 core 投影改为 `steward_delivery` 发布后交付，本包按 design §3 适配，不重新引入长事务。

相关 pytest 覆盖 `test_steward_assist.py`、`test_steward_suggestions.py`、`test_family_recommendations.py`、实际行为投影回归和新增证据版本测试；静态检查执行 ruff / format / mypy。变化涉及迁移时先在隔离旧数据样本上 upgrade。

完成标准为 SP-AC1～6 有证据；未实现项保持未完成。主分支集成、部署和破坏性清理需按届时授权范围处理，不能用规划记录代替验证。

## MR-26 检查完成

`steward.py` 已将重建所属前缀与全局写白名单分开，两个删除分支采用大小写敏感的 `substr` 等值谓词，三个事件处理器共用前缀定义。真实推荐忽略链、无事件与近似键、跨账号/空间及重复重放共 12 项相关测试通过（1.86 秒）；Ruff check/format 和 mypy 204 个源文件通过。独立 `trellis-check` 未发现问题，未修改代码；见 research/validation.md。新合同记录在 `.trellis/spec/backend/steward-behavior-rebuild.md`。
