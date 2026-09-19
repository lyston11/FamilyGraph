# Implement：串行修复与验收步骤

## 当前状态

2026-09-19：仅完成规划，不代表代码或生产问题已解决。任务保持 planning；没有执行 `task.py start`，没有任务分支/worktree，没有部署、模型调用或生产写操作。

规划校验：`task.py validate` 已通过，implement/check 各 8 条有效上下文；现有 `steward-action-card.md` 为 42739 bytes，超出 32768 bytes 注入上限，实施及核验均须分段读全文。已检查本任务无 TBD/TODO 占位。本轮未运行业务测试，以上校验仅证明规划上下文有效，不是代码验收。

实施顺序固定为 S0 -> S1 -> S2 -> S3 -> S4 -> S5 -> S6。A/B 共用文件，禁止并行修改；生产步骤 S7 需单独授权。各步完成后即时更新证据和清单，失败不能勾选完成。

## S0：审阅、激活与隔离

- [ ] 用户审阅 PRD/design 并批准实施，特别是“任意共同 household 的内部布尔抑制”相对旧本空间规则的有限变更。
- [ ] 在主检出核对 `git status --short`、`git worktree list`、任务 current 与同模块在途任务；保全其他会话修改，不覆盖旧任务文件。
- [ ] `python3 ./.trellis/scripts/task.py validate .trellis/tasks/09-19-steward-recommendation-correctness`。
- [ ] 只在获准后运行 `python3 ./.trellis/scripts/task.py start .trellis/tasks/09-19-steward-recommendation-correctness`，读取 task.json 的 branch/worktree_path，进入该 worktree 后写业务代码。
- [ ] 读取 manifest 的 Spec 全文；超过注入上限的 `steward-action-card.md` 分段补全。核对当前源码和 caller，不按历史行号照抄。
- [ ] 后端命令显式使用该 worktree 的 `PYTHONPATH=.` 与解释器；核对 `app.__file__`。所有自建测试/演练环境显式 `DATA_DIR=<新隔离目录>`，不是 `DATABASE_URL`；禁止默认写生产库。

回滚点：此阶段仅创建隔离现场；规划变更可停在 planning，不能把未合并分支强制删除。

## S1：先写失败回归和边界对照

- [ ] 在现有测试夹具中建立 A->B 亲子、A->P->B 祖孙、生物长链、反向 sibling、环输入；配套无冲突/guardian/混合继养长链对照，不采用真实个人数据。
- [ ] fake candidate transport 同批输出错误与正常候选，沿 staged core -> assist -> 后续 core/delivery 运行，捕获当前错误公开投影的红测。
- [ ] 建立 lineage 中两配偶 + 别处共同 household active 成员夹具，证实当前仍会错误产生 household_link；另保留 request_lineage 合法场景。
- [ ] 种入旧 pending/viewed/accepted 卡与旧 proposed suggestion、active inferred edge，证据 hash 保持不变；构造 list/detail/通知/提交/转正/执行回归。
- [ ] 保存红测命令、失败断言与基线 SHA 到 `research/evidence/red-tests.md`，确认失败原因来自目标 bug 而非夹具或环境。

验收映射：AC1/2/4/6；本阶段不调用真实模型、不触碰生产。

## S2：候选安全判据及所有公开消费者

- [ ] 实现 design §2 的共享负向判据，限定 confirmed、本空间/global 与节点范围；单批构造图、迭代去环，不调用无范围的全局祖先 helper。
- [ ] 在 suggestion 和 inferred 两个投影入口接线，逐条抑制；不能整批丢弃合法条目，不改变原候选 payload/status/归因身份或正向证书。
- [ ] 对旧 delivery candidate 意图执行时重验，并保持 versioned evidence 意图的独立内部路径。
- [ ] 建议 effective_state/source_state、allowed actions、notifications 保持一致；推测 active/list/detail/overlay 排除当前错误边。GET 断言零写、零网络。
- [ ] submit_suggestion、confirm_edge、reinstate_edge 在原写事务内重验；陈旧 revision/撤权/证据变化仍按原错误优先级处理，无 SourceFact/确认副作用。
- [ ] 为未变 hash 的 inferred_review 加语义退役；保留用户 dismissed/rejected/confirmed、candidate 历史与已读状态。已 submitted/confirmed 来源只报告人工核查，不批量撤销。
- [ ] 复验 versioned 双向粘性隔离、无支撑无冲突 unsupported 正常公开路径、同批部分成功、超预算 fail-closed、授权范围不外溢。

验收映射：AC1/2/3/5/8。优先提交为单独小步代码提交，供后续共同家庭变更定位回归。

## S3：共同家庭判断贯穿生成、读取及执行

- [ ] 在 `steward.py` 以两个 SpaceMember 别名 + household kind + active 的 EXISTS 实现 bool 查询；只返回布尔值。
- [ ] `_pair_inputs` 不再按当前 space.kind 门控 shared household；lineage eligibility、creation_choices、disclosure、cooldown 保留。
- [ ] 更新 matrix 字段合同及测试，验证只去掉 create_household，request_lineage 不受误伤。
- [ ] 同时覆盖旧推荐复核与 staged `recommend/card_review`；复核真正调用 supersede FSM，accepted 卡也能退役，终态不复活。
- [ ] ActionCard API/notification 共用只读有效状态，错误旧卡不再进入待处理；accept 前校验，execute 在原事务调用空间命令之前再次查询，409 时不声称已提交 supersede。
- [ ] 验证“GET 后别处加入共同家庭再 execute”“意图 prepare 后加入再 delivery”“两个旧卡并发 execute”均无重复家庭；重验必须与写入原子化，若采用 SQLite writer 锁则在取得 writer 后重验，不能把锁前只读结果当作最新资格。通用自主创建流程保持原行为。
- [ ] 验证共同 lineage、profile refs、pending/rejected/退出、仅一端 active、多 household、退出最后一个共同 household 的对照；不得依赖 refs 表非空。

验收映射：AC4/5/6。记录 SQL 查询范围及响应/日志哨兵，确保其他空间名称/ID/成员不外泄。

## S4：存量收敛、版本与失败恢复

- [ ] 核实策略/config/overlay 版本接线；如需 bump 策略，证明未知模型请求不重放、既有失败预算不被无意清零、正向证书不改写。
- [ ] 证明已有 integrity_scan 每代仍准备 card_review/recommend/inferred_review，未变 hash 也复核；不新建全局 fan-out 或维护队列。
- [ ] 模拟其他 household 成员资格变更但当前空间 revision 未变：读与命令立刻生效，下一次健康扫描/交付持久收敛。
- [ ] 构造两个 worker、旧 lease/attempt、用户并发 dismiss/accept、意图重复交付、事务回滚；旧执行者不得覆盖新状态，重复回放无新增通知/家庭。
- [ ] 在隔离旧数据样本输出 dry-run：各对象总数、实际命中数、对象 ID/状态/理由、受保护历史摘要；一条候选多个建议/收件人分别统计。
- [ ] 经正规 job/review 完成隔离收敛，再次 dry-run 命中活跃可行动对象为零；已提交/已确认例外单列，不宣称已自动处理。
- [ ] 前后逐字段比较 candidate/version、事实、已确认提案、私人忽略、共享驳回、read/cooldown 与终态卡；允许变化仅为设计列明的派生状态与其合法事件。

验收映射：AC6/7/8。禁止按“26/21”数字或姓名做清理条件。

## S5：分层质量门禁

先运行窄回归，再扩展到共享链路。以下均在任务 worktree 的 `backend/` 执行；解释器/venv 缺失时先按项目依赖工具建立，不借用错误 checkout 的 app。

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_suggestion_quality.py tests/test_steward_inferred.py tests/test_steward.py tests/test_action_cards_api.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_guard.py tests/test_steward_suggestions.py tests/test_steward_candidate_evidence.py tests/test_steward_candidate_evidence_integration.py tests/test_steward_candidate_evidence_migration.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_staged_pipeline.py tests/test_steward_delivery_recovery.py tests/test_steward_snapshot_fences.py tests/test_steward_input_versions.py tests/test_steward_publication_consumers.py tests/test_action_cards_core.py tests/test_notifications.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
PYTHONPATH=. .venv/bin/python -m pytest -q
```

- [ ] 新建 policy 测试文件若使用独立文件，加入首轮命令；不得只运行旧测试后声称新行为覆盖。
- [ ] 完整后端检查通过；失败逐条归因，不把环境 blocked 当 passed。
- [ ] 如最终无需 schema 改动，记录 Alembic head 未变；若引入迁移，先重新审阅设计，隔离库 upgrade head/旧数据/FK/降级拒绝全部验证，不猜序号。
- [ ] 运行隔离 API smoke：在仓库根 `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-recommendation-smoke.json`，先确认脚本隔离 DATA_DIR 和端口；退出码 2 是阻塞。
- [ ] 隔离浏览器：登录合成配偶/亲子账号，在通知与待办、建议详情、推测树、卡片旧链接检查状态、计数及按钮；强制使用隔离后端，不把本机 8000 的 SSH 隧道当本地服务。
- [ ] 至少一个桌面与移动视口截图；确认错误条目退出可行动区，正常 sibling/共建/lineage 项仍保留，无重复通知或实际家庭创建。
- [ ] 如确需改前端，补相关 Vitest，运行 `npm run lint`、`npm run type-check`、`npm test`、`npm run build`；未改则记录未跑完整前端构建的原因。
- [ ] 独立 `trellis-check` 检查全部差异、Spec 一致性及 AC 逐项证据。dispatch 首行必须为 `Active task: <task.py current 返回路径>`。

把命令、版本、结果、环境限制写入 `research/evidence/validation.md`，摘要写入 `research/validation-summary.md`。失败或未跑项不得标通过。

## S6：规范、提交与串行集成

- [ ] 增补精确 Spec 叶：候选负向抑制、共同 household 布尔边界、GET/命令/后台收敛一致性及必要回归；索引仅加路由链接，不复制正文，不重写旧归档任务。
- [ ] 更新本任务 AC 证据映射，检查 docs 中没有把计划写成完成；`task.py validate` 通过。
- [ ] 在任务分支小步 commit 并 push 或本地 backup 钉住；不提交其他任务脏文件，不 reset/rebase/force push。
- [ ] 由单一集成通道在主检出 merge，复查迁移头与受影响回归，`git push origin main`；仅本地 merge 不算服务器可同步。
- [ ] 若授权范围仅实现，明确交付“代码完成、线上未部署/未收敛”；不能把 S7 勾选通过。按实际完成范围决定归档时点，未完成的验收不得消失。
- [ ] 达到任务关闭门槛后 archive；分支已合并且 worktree 无未提交代码时，立即 `git worktree remove <task worktree>` 和 `git branch -d <task branch>`，报告结果。不满足条件保留现场并说明，禁止强删。

## S7：生产部署与存量收敛（单独授权）

- [ ] 再次获得明确生产部署及写入授权，记录基线代码 SHA、实际服务工作目录/systemd 作用域、DATA_DIR、schema head 与相关列、有效开关。不能只看 Git HEAD 或 health。
- [ ] 用 `python -m app.backup` 或 SQLite online backup 做一致备份；服务运行中不 cp 主库。权限受控保存备份，不进仓库。
- [ ] 从在线备份恢复到显式隔离 DATA_DIR，运行同一 dry-run 与正规服务链演练；不得在远端 backend 默认环境跑写库测试。
- [ ] 形成生产只读预览清单，核对“待处理总量 != 无效命中量”；朱元璋/马皇后仅作复查样本，不硬编码 ID 批量更新。
- [ ] 部署并核对实际进程已加载修复；如有迁移先按演练流程执行。只同步 Git 的 timer 不算部署完成。
- [ ] 按空间通过现有管理员 rerun/健康扫描逐批收敛；遵守鉴权、幂等键、冷却和租约，不绕过公开业务流程直接写状态。
- [ ] 对前端原截图入口复验：无错误 sibling 待核实、无已共享家庭的共建待办、正常建议保留；记录前后各类计数、人工核查例外和生产事实/成员不被误改。
- [ ] 观察至少一次成功扫描及交付周期，确认不重生、不通知风暴、队列不积压；模型功能未关闭也不能绕过负向判据。未做真实模型质量评测时如实说明。

生产回退：停止新批次、保留证据及所有历史，优先向前修复；必要暂停需授权。不可恢复旧错误候选为 pending，不用整体数据库回滚覆盖期间用户写入。
