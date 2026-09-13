# Implement — Steward 短事务重算与渐进家谱加载

状态：规划待最终评审，尚未实施。依赖 [prd.md](prd.md)、[design.md](design.md)、[research/current-state.md](research/current-state.md)。清单项均未完成；上下文校验不等于代码、迁移或性能检查通过。

## 0. 启动与协作边界

- [ ] 用户在最终规划摘要之后批准实施，再从主检出运行 task.py start。
- [ ] 核验 task.json 的 branch/worktree_path；业务代码、迁移和测试在该 linked worktree。任务文档/生命周期在主检出，给执行/检查 agent 传入主检出任务目录绝对路径。
- [ ] 记录启动 HEAD、dirty 文件及并行任务，不纳入 config.py、0041 等既有修改。
- [ ] 与 kinship-presentation/terminology-autonomy 的共享 PFV/Terms/Steward 文件和 migration head 串行协调。一个一致性任务、一个分支；下列步骤有依赖，不能直接并行写。
- [ ] 主线程维护契约，按步骤派发 trellis-implement，独立 trellis-check 审查；明确有限写入范围，子代理不再派发实现/检查链。

## 1. 基线和完整输入合同（后续步骤前置）

- [ ] 隔离 SQLite WAL 库复现慢计算时第二连接登录/lease/maintenance 写入受阻，保留原代码失败证据。禁止触碰线上库或复制运行中的 SQLite 主文件。
- [ ] 记录 30 人六代、50/200 稀疏多代树冷/热耗时和 SQL 量；稠密、断开图单列资源压力。
- [ ] 完成输入生产者清单：事实、账号/成员/ref、披露、性别/出生、词典/偏好、bridge、推测状态/开关、算法/策略和纯时间变化边界。逐项指定版本键、失效范围与回归案例。
- [ ] 定义不可变 snapshot/result/continuation DTO、结构/展示指纹、valid_until 和 fresh Session/显式读事务合同。

重点：relationship_graph.py、relationship_resolver.py、derived_facts.py、personal_family_view.py、domain_events.py、visibility.py、terms.py、推测/设置写入口。

## 2. 存储与纯计算（依赖 1）

- [ ] 按当前 Alembic head 新建加法迁移，定义 input revision、generation、结果唯一键、跨代 retry budget、publication、delivery intent 的约束/索引。
- [ ] 隔离库验证 upgrade、历史缓存失效、保留数据的 downgrade 约束和重升级，再跑受影响测试。
- [ ] 抽离共享纯计算，批量读取一次 viewer 授权输入、复用图、完整指纹缓存前置。
- [ ] 统一可达性预检查和确认骨架，复用深度/partner/bridge 规则；合法节点/连线集合前后等价。
- [ ] 路径搜索有界续算/取消，保持主替路径排序与亲缘语义；完成/no_path/未完成/超预算分离。
- [ ] 结构结果供 DerivedFact/PFV 共用；普通读取不消费未发布 staging；展示变化只重做展示，推测独立失效。

回退点：尚未切换生产执行器，新表/新路径独立验证，不改变业务事实和用户动作。

## 3. 协调器、短事务与恢复（依赖 2）

- [ ] 替换整族立即事务：lease → 显式短读 snapshot → 纯 CPU 调度 → 短 save_batch → publish。
- [ ] 有界 spawn 执行器、全局活跃租约上限、空间/查看者轮转、独立 heartbeat、内存/续算上界及关闭恢复。
- [ ] 按 design.md 状态转换矩阵实现 fresh-read/CAS：正常计算要求有效 fence，claim 使用 queued 前提，supersede/reaper 使用真实失效条件；防止旧 owner/attempt 的迟到失败或回收覆盖新租约。
- [ ] 固定必需工作和消费上界；原子保留同水位 demand、更高水位和版本漂移需求。
- [ ] 批次唯一键/完成计数幂等；publish 只做有界版本/计数检查、切指针、结算水位、激活待办并登记后继。
- [ ] 恢复仅复用输入一致的完整批次；旧代/缓存回收和 reaper 有界执行。
- [ ] 必需目标预算耗尽时整代 failed、不发布/不推进水位；跨 generation/扫描/重启复用同输入失败预算，人工重试仅增加一次有界机会。
- [ ] 无变化图重算短路与到期检查/恢复分开；整矩阵预热移出页面关键路径。

重点：steward.py、maintenance.py、models/steward.py、新 generation/执行器模块、config.py 的本任务配置、连接使用点。

## 4. 核心检查与发布后交付（依赖 2、3）

- [ ] 将 _execute_locked 每项职责映射到纯计算/分批准备/发布/交付，覆盖 finding、卡片、suggestion、inferred、assist 和到期。
- [ ] generation 门控交付，publish 前零外部可见副作用；DB 结果与 intent done 同事务，业务幂等沿用证据键。
- [ ] 用户动作 revision 冲突重验/安全跳过；不覆盖提交、忽略或撤销。
- [ ] 交付有限退避/独立恢复；assist 继续 job succeeded/provider/预算/证据 fence，HTTP 在事务外，未知网络结果沿用已有恢复合同。
- [ ] 管理端状态/schema 区分核心完成与交付积压；如涉及管理前端展示，同步其类型/decoder。

回退点：停执行器时保留已发布结果/待办，不自动 fallback 到旧长事务执行链。

## 5. 渐进 API 与前端（依赖 2–4）

- [ ] GET progressive=true 显式启用；旧请求原合同。同步 Pydantic/TS 和 runtime decoder。
- [ ] 认证 demand/focus/retry 入口复用 canonical enqueue，同 scope 幂等，不接受任意 viewer/priority 或隐藏目标。
- [ ] 同代确认骨架、累积完整结果、真实进度、有效期；本人 ready 不冒充全局 current。
- [ ] 每次读取/304 先授权、输入、时间、中间路径复核；推测开关/证据单独重验。明确展示有效期响应头、CORS 暴露和 304 续期验证。
- [ ] store 账号/空间隔离、单更新链、请求序号和 generation/revision 防倒退；切换/注销/权限错误清理。
- [ ] 首骨架有界快速轮询，后续约 1 秒，ready/current 低频保活，隐藏暂停/前台重验、断网退避和展示期限。
- [ ] 同 topology_revision 只补标签；保留拖动/缩放/选中，面板按稳定 ID；首屏或主动操作才 fitView。
- [ ] 推测层后续出现不阻塞确认骨架/称谓，不把推测连线画成确认事实。

重点：后端 PFV API/schema/service；前端 personalFamilyView API/types/store、FamilyTreeView.vue、useFamilyTreeCanvas.ts、familyTreeLayout.ts 与对应测试。

## 6. 验证矩阵（依赖全部实现）

| Scenario | Necessary evidence | AC |
| --- | --- | --- |
| 慢计算 + 并发写 | 独立连接真实提交，同步点暂停计算时写入完成；不能只验证 monkeypatch 没抛错 | 1 |
| 缓存/纯计算等价 | 命中不搜索，同 viewer 只构图一次，主替路径/深度/partner/bridge 等价 | 3、5 |
| 输入/时间改变 | 逐生产者变更和仅推进时钟；旧批次/304 拒绝，字段遮罩/权限正确 | 2、6 |
| 租约竞争 | 同 owner 旧 attempt、新 owner、deadline、迟到 heartbeat/失败回队/保存/发布不覆盖新执行者 | 2 |
| 批次/崩溃恢复 | 提交前后丢失回执、重复批次、重启、publish rollback、必需目标耗尽及跨代预算，进度/水位幂等 | 2、4、7 |
| 同水位 demand | lease/封口前后需求、清单内提升顺序；后继不丢失，不凭水位短路 | 4、5 |
| 调度/资源 | 大空间中小空间推进，单 pair 让出/取消，预算不当 no_path，清理不长锁 | 1、4、5 |
| 交付 | publish 前零交付，发布后崩溃恢复、用户动作冲突、有限重试、模型 fence；HTTP 已发送未落结果时进入 unknown 且不通用重发 | 7 |
| 浏览器 | 骨架首屏、标签批次、布局、错误、乱序/旧代、隐藏/恢复、账号切换/撤权 | 3、4、6 |
| 协议/迁移 | 新旧客户端、ETag/有效期、旧缓存失效、upgrade/受限 downgrade/upgrade | 6、8 |

先跑新增和直接受影响测试；最后检查整个后端及家庭前端。若修改 system-admin-frontend，再跑该包完整检查。通过后仅新改动、失败或未解决疑点触发重复检查。

在任务 worktree 的 backend/ 目录：

~~~bash
ruff check .
ruff format --check .
mypy app
pytest
~~~

在任务 worktree 的 frontend/ 目录：

~~~bash
npm run lint
npm run type-check
npm test
npm run build
~~~

迁移使用显式隔离 DATABASE_URL，先 upgrade head，再测试。API smoke 从 worktree 根目录运行 ./scripts/frontend-api-smoke.sh --report /tmp/familygraph-steward-smoke.json；退出码 2 只能记环境阻塞。端口/数据库不得和其他任务共用，完整开发环境只选择一种。

容量证据包含 30/50/200 稀疏图冷/热数据、在线请求延迟、持锁分布、首骨架/首批称谓/正式发布时间、CPU/队列/交付延迟、环境和原始样本数量；至少两次实际 300 秒扫描周期。稠密/断开图单列。不能用“禁用 worker 后无 500”替代 AC1。

## 7. 全量审查、发布与清理

- [ ] trellis-check 审查全任务 diff、AC1–AC9、授权/事件/推荐语义、类型同步和迁移冲突，修复后完成门禁。
- [ ] 将短事务/输入版本/预览与发布合同记录到 .trellis/spec/ 的适当文档，按当前 AGENTS.md 执行，区分历史材料与新实现。
- [ ] 主线程准备本任务提交/集成范围，不带入未识别 dirty 文件；对外推送、合并、部署按已获授权执行。
- [ ] 发布前准备健康检查、回滚包及合法备份；先后端兼容，再渐进 API/前端，小范围观察后扩展；未经授权不操作生产。
- [ ] 发布核验 busy_timeout=5000、核心 API、worker 进度、delivery 积压及两次扫描；失败停止扩展，按 design.md 停 worker/关闭渐进回退。
- [ ] 合并归档后确认无未提交代码，立即 git worktree remove / git branch -d 清理；条件不满足保留并说明，不强制删除。

本轮仅提交规划评审；task.py start、业务实现、迁移、性能测试、提交和生产操作尚未执行。

## 8. 实施记录（2026-09-14，主线程单 agent 执行）

本轮已实施并验证（任务分支 `feat/09-13-steward-snapshot-progressive-recompute`，commit ad870c8）：

- 步骤 3（核心）：`steward.run_steward_job` 拆为短事务流水线——开始栅栏（短立即事务置 running）→ 写锁外 CPU 计算（事件窗口读取、路径解析）→ 分块短写事务落库（`STEWARD_DERIVED_COMMIT_CHUNK`，默认 50 对/块）→ 独立租约心跳线程（TTL/3 周期，自有 Session）→ 发布阶段 fresh-read 完整租约栅栏 + settle。`derived_facts` 新增 `compute_pair`/`apply_pair_result`（计算/写入分离，`get_or_compute` 语义不变）。`rebuild_space_views(per_view_commit=True)` 逐视图独立提交。
- 步骤 5（部分）：GET `/api/personal-family-view?progressive=true` 返回 `progress` 块（contract_version pfv-progress-v1、phase、generation/revision、completed/total、next_poll_ms）；缺省响应不含 progress（`response_model_exclude_unset`）。前端：结果未就绪按 next_poll_ms 轮询、首屏才 fitView（称谓批次不重排视口）、无称谓节点显示「整理中」、decoder/store/types 同步。
- 检查：backend ruff/format/mypy 通过；pytest 1055 passed（含新增 `test_steward_short_tx.py`——慢计算期间独立连接写入 <5s 完成的 AC1 核心回归、发布栅栏、发布结果等价；`test_personal_family_view_progressive.py` 7 项合同）；frontend lint/type-check/test(583)/build 全通过。未运行 frontend-api-smoke（需完整开发环境，本机未起 dev-up）。

第二轮（2026-09-14，commit 0baf299）已实施：

- 迁移 0044（`steward_generations`/`steward_generation_views`/`steward_retry_budgets`）：隔离库 upgrade → downgrade → 重升级验证通过。
- 执行器接入：每代次登记发布行（指纹+游标）；无变化指纹短路结构重算（有 failed 视图的代次不算完整发布）；per-viewer 真实进度行；必需阶段失败按指纹跨代记账，耗尽整代失败（不发布/不推进水位）。
- 渐进 API：progress 优先读代次进度行（generation/revision 单调，stale running → retrying）；POST `/personal-family-view/demand`（成员 404 fail-closed、focus 骨架内校验 422、活跃作业合并）；200/304 签发 `X-PFV-Display-Until`（默认 300s）。
- 推测层重验增强（effective_enabled + viewer_path 逐步重验）；admin status 增 `delivery_backlog`/`latest_generation`。
- 基准脚本 `scripts/benchmark-steward-recompute.py`（隔离临时库）。实测（本机 macOS/SQLite WAL/busy_timeout=5000）：30 人冷 13.4s / 热 0.019s（短路生效）；50 人冷 60.0s / 热 0.027s；冷算期间并发写 0 失败，p95 9.6–15.7ms、p99 19.9–27.9ms（≤100ms 目标达标）；max 偶发 525ms（30 人）/1387ms（50 人）尖峰——疑似 WAL autocheckpoint/fsync，属 AC1「单次 >500ms 必须整改项」，整改方向：视图行分块应用与 checkpoint 调优（未在本轮实施）。200 人样本与真实 300 秒扫描窗口观测未执行（脚本已支持）。
- spec 更新：`.trellis/spec/backend/steward-action-card.md` §9（短事务/generation/demand 合同）。

仍未实施（后续轮次）：

- 全 staging + publication 指针读路径切换（当前 PFV live 行按视图原子重建，跨视图代次一致性靠指纹+后继扫描收敛）；
- delivery intents 独立表（辅助交付沿用 StewardAssistBatch 状态机）；
- admin_rerun 预算放宽的 API 化；max 尖峰整改（见上）。
