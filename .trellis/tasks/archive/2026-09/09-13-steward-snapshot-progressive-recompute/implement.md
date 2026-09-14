# Implement — Steward 短事务重算与渐进家谱加载

状态：实现、主线接合及最终本地 AC1–AC9 验收完成，已合入本地 main、归档并清理任务 worktree/分支。依赖 [prd.md](prd.md)、[design.md](design.md)、[research/current-state.md](research/current-state.md)。§8–11 为历史提交和中间检查点；最终状态见 §12 及 [集成验收](research/integration-acceptance.md)。

## 0. 启动与协作边界

- [x] 任务已在 in_progress，继续已确认的根治与“先骨架后称谓”范围，不重复创建任务或请求实施授权。
- [x] 核验 task.json 的 branch/worktree_path；业务代码、迁移和测试在该 linked worktree。任务文档/生命周期在主检出，给执行/检查 agent 传入主检出任务目录绝对路径。
- [x] 续作基点为 0baf299；保留主检出 config、0041、AGENTS 和其他任务改动，未混入本任务分支。
- [x] 核验共享 PFV/Terms/Steward 文件及 migration head：启动时记录的迁移分支已在用户授权归档后经 0048 串行接合，既有 revision ID 保留，最终为单 head。
- [x] 主线程维护契约，按步骤派发 trellis-implement，独立 trellis-check 审查；明确有限写入范围，子代理不再派发实现/检查链。

## 1. 基线和完整输入合同（后续步骤前置）

- [x] 隔离 SQLite WAL 库复现慢计算时第二连接登录/lease/maintenance 写入受阻，保留原代码失败证据。禁止触碰线上库或复制运行中的 SQLite 主文件。
- [x] 记录 30 人六代、50/200 稀疏树冷/热、写事务样本数和慢事务 SQL 计数；稠密/断开图用资源与语义回归单列。全量 SQL 明细及连续 CPU/RSS 未采样，限制写入最终报告。
- [x] 完成输入生产者清单：事实、账号/成员/ref、披露、性别/出生、词典/偏好、bridge、推测状态/开关、算法/策略和纯时间变化边界。逐项指定版本键、失效范围与回归案例。
- [x] 定义不可变 snapshot/result/continuation DTO、结构/展示指纹、valid_until 和 fresh Session/显式读事务合同。

重点：relationship_graph.py、relationship_resolver.py、derived_facts.py、personal_family_view.py、domain_events.py、visibility.py、terms.py、推测/设置写入口。

## 2. 存储与纯计算（依赖 1）

- [x] 按当前 Alembic head 新建加法迁移，定义 input revision、generation、结果唯一键、跨代 retry budget、publication、delivery intent 的约束/索引。
- [x] 隔离库验证 upgrade、历史缓存失效、保留数据的 downgrade 约束和重升级，再跑受影响测试。
- [x] 抽离共享纯计算，批量读取一次 viewer 授权输入、复用图、完整指纹缓存前置。
- [x] 统一可达性预检查和确认骨架，复用深度/partner/bridge 规则；合法节点/连线集合前后等价。
- [x] 路径搜索有界续算/取消，保持主替路径排序与亲缘语义；完成/no_path/未完成/超预算分离。
- [x] 结构结果供 DerivedFact/PFV 共用；普通读取不消费未发布 staging；展示变化只重做展示，推测独立失效。

回退点：尚未切换生产执行器，新表/新路径独立验证，不改变业务事实和用户动作。

## 3. 协调器、短事务与恢复（依赖 2）

- [x] 替换整族立即事务：lease → 显式短读 snapshot → 纯 CPU 调度 → 短 save_batch → publish。
- [x] 有界 spawn 执行器、全局活跃租约上限、空间/查看者轮转、独立 heartbeat、内存/续算上界及关闭恢复。
- [x] 按 design.md 状态转换矩阵实现 fresh-read/CAS：正常计算要求有效 fence，claim 使用 queued 前提，supersede/reaper 使用真实失效条件；防止旧 owner/attempt 的迟到失败或回收覆盖新租约。
- [x] 固定必需工作和消费上界；原子保留同水位 demand、更高水位和版本漂移需求。
- [x] 批次唯一键/完成计数幂等；publish 只做有界版本/计数检查、切指针、结算水位、激活待办并登记后继。
- [x] 恢复仅复用输入一致的完整批次；旧代/缓存回收和 reaper 有界执行。
- [x] 必需目标预算耗尽时整代 failed、不发布/不推进水位；跨 generation/扫描/重启复用同输入失败预算，人工重试仅增加一次有界机会。
- [x] 无变化图重算短路与到期检查/恢复分开；整矩阵预热移出页面关键路径。

重点：steward.py、maintenance.py、models/steward.py、新 generation/执行器模块、config.py 的本任务配置、连接使用点。

## 4. 核心检查与发布后交付（依赖 2、3）

- [x] 将 _execute_locked 每项职责映射到纯计算/分批准备/发布/交付，覆盖 finding、卡片、suggestion、inferred、assist 和到期。
- [x] generation 门控交付，publish 前零外部可见副作用；DB 结果与 intent done 同事务，业务幂等沿用证据键。
- [x] 用户动作 revision 冲突重验/安全跳过；不覆盖提交、忽略或撤销。
- [x] 交付有限退避/独立恢复；assist 继续 job succeeded/provider/预算/证据 fence，HTTP 在事务外，未知网络结果沿用已有恢复合同。
- [x] 管理端状态/schema 区分核心完成与交付积压；如涉及管理前端展示，同步其类型/decoder。

回退点：停执行器时保留已发布结果/待办，不自动 fallback 到旧长事务执行链。

## 5. 渐进 API 与前端（依赖 2–4）

- [x] GET progressive=true 显式启用；旧请求原合同。同步 Pydantic/TS 和 runtime decoder。
- [x] 认证 demand/focus/retry 入口复用 canonical enqueue，同 scope 幂等，不接受任意 viewer/priority 或隐藏目标。
- [x] 同代确认骨架、累积完整结果、真实进度、有效期；本人 ready 不冒充全局 current。
- [x] 每次读取/304 先授权、输入、时间、中间路径复核；推测开关/证据单独重验。明确展示有效期响应头、CORS 暴露和 304 续期验证。
- [x] store 账号/空间隔离、单更新链、请求序号和 generation/revision 防倒退；切换/注销/权限错误清理。
- [x] 首骨架有界快速轮询，后续约 1 秒，ready/current 低频保活，隐藏暂停/前台重验、断网退避和展示期限。
- [x] 同 topology_revision 只补标签；保留拖动/缩放/选中，面板按稳定 ID；首屏或主动操作才 fitView。
- [x] 推测层后续出现不阻塞确认骨架/称谓，不把推测连线画成确认事实。

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

迁移使用显式隔离 DATA_DIR（DATABASE_URL 由其派生），先 upgrade head，再测试。API smoke 从 worktree 根目录运行 ./scripts/frontend-api-smoke.sh --report /tmp/familygraph-steward-smoke.json；退出码 2 只能记环境阻塞。端口/数据库不得和其他任务共用，完整开发环境只选择一种。

容量证据包含 30/50/200 稀疏图冷/热、在线请求、持锁分布、首骨架/首批称谓/正式发布时间、环境和样本数量；至少两次实际 300 秒扫描周期。稠密/断开图单列资源与语义回归。本次不包含连续 CPU/RSS、全量 SQL 或独立的排队/交付时延采样；不把重算墙钟耗时叫 CPU 时间。不能用“禁用 worker 后无 500”替代 AC1。

## 7. 全量审查、发布与清理

- [x] 独立审查全任务 diff、AC1–AC9、授权/事件/推荐语义、类型同步和迁移冲突；接合及两轮性能整改复核无阻塞，见 research/final-review.md 与 research/integration-review.md。
- [x] 将短事务/输入版本/预览与发布合同记录到 .trellis/spec/ 的适当文档，按当前 AGENTS.md 执行，区分历史材料与新实现。
- [x] 三组工作、串行接合及性能整改均已提交，清单见 research/work-commits.json；主检出其他 dirty 文件不纳入本任务。用户已授权本地合并、归档和清理，未授权推送或部署。
- 后续部署范围：目标服务器健康检查、回滚包与合法备份；先后端兼容，再渐进 API/前端，小范围观察后扩展。这些生产操作不属于本次本地归档的未完成代码工作。
- 后续部署验收：核验目标环境 busy_timeout=5000、核心 API、worker 进度、delivery 积压及两次扫描；失败停止扩展，按 design.md 停 worker/关闭渐进回退。本地对应场景已验证。
- [x] 已 fast-forward 合入本地 main，task.py archive 完成；在任务 worktree 干净、分支已合并的前提下，git worktree remove 与 git branch -d 均成功，未使用强制删除。共享 .venv/node_modules 保留。

以下 §8–11 保留各时点的历史提交、失败和验收记录；其中的“待确认”“未授权”“待验证”只描述当时状态，当前结论以 §12 为准。

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

以下是 0baf299 时点仍缺少的必需工作，续作已补齐，最终证据见 §11：

- 全 staging + publication 指针读路径切换（当前 PFV live 行按视图原子重建，跨视图代次一致性靠指纹+后继扫描收敛）；
- delivery intents 独立表（辅助交付沿用 StewardAssistBatch 状态机）；
- admin_rerun 预算放宽的 API 化；max 尖峰整改（见上）。

## 9. 续作边界与源码核验（2026-09-14）

在干净任务 worktree 的 0baf299 核验确认：现有 generation 只记录进度，PFV 与 DerivedFact 仍提前写 live 表；PFV 在 flush/delete 后搜索，单 viewer 仍持长写锁；首次必需视图失败仍可结算。focus 只记录日志，同水位需求未持久保存。前端未消费展示有效期，未按 generation/revision 拒收倒退，未实际展示骨架预览。这些是原设计未完成部分，不能归档或仅作为可选后续。

本次实现责任边界：

- 后端计算原语：relationship_graph / relationship_resolver / derived_facts / terms，抽取无 Session 的图解析和词典快照，前置缓存，保留路径排序与语义，增加可让出的搜索预算。
- 后端发布链：steward / generation / PFV / maintenance 及本任务新迁移，显式一致读快照、完整输入 fence、版本化目标结果和 publication、持久 demand 与交付待办、按 target 有界提交及失败恢复。
- 家庭前端：PFV API/types/store 与 FamilyTreeView/canvas，真实骨架/称谓进度、请求与版本排序、有效期/网络/可见性生命周期、focus/retry 与坐标/选中保持。
- 验证脚本：用有账号且在树内的 viewer，保留 30 人六代回归并增 50/200 人；分别记录 DB 持锁、在线请求和首骨架时间，覆盖真实扫描窗口。旧 probe 结果不能直接当作持锁分布，WAL 归因尚未建立。

不改种子数据、busy_timeout、既有亲缘语义；不改生产配置、不部署、不合并归档、不收录其他任务 dirty 文件。局部重构以原 resolver 黄金测试及跨路径等价测试验证；安全/并发以独立连接、发布故障与迟到 worker 检验。完成标准仍为 AC1–AC9，未通过项保留明确证据。

## 10. 续作验证记录（未完成最终验收）

- 冻结 `0baf299` 源码的独立基线已复现：朱氏 30 人 / 30 账号 / 45 条事实，冷算 51.9462s，3 次登录 500，隐式写锁持有下界 p99/max 1003.181ms。旧“树外唯一账号”基准与 WAL 推测不能用作通过证据；原始数据和测量器校准见 [续作审计](research/implementation-audit-0baf299.md)。
- 当前 `0045_steward_staged_publication` 隔离库升降重升通过；running 代次、pending/failed 交付阻止回退；完成交付后回退/重升保留 3 用户和 2 条真源事实，旧 PFV 缓存失效，外键检查通过。迁移仍可能随审查修订，最终按文件摘要复核。
- 主线程拥有 `backend/tests/test_steward_snapshot_fences.py`，11 项通过：双连接一致读快照；事实/成员/性别/出生/名称变化拒绝旧结果；重复回执只计一次；同 owner 新 attempt / 新 owner / 过期租约隔离；发布 flush 后故障使 publication、job、水位、完成事件及交付激活一起回滚，重试只发布一次。
- 纯计算关联集 121 passed：仅词典变化复用已发布结构的缺口已修复；算法版本变化同时失效结构复用与对应预算。
- 家庭前端已取得一轮完整 lint/type-check/632 tests/build 通过；后续小修仍需最终检查，实际浏览器与完整 API 尚待接通验证。API smoke 脚本会自行启动隔离 listener，无需用未启动 dev-up 作为阻塞理由。
- 后续验收：新代码的朱氏 30 / 稀疏 50、200 冷热并发、两次真实 300 秒扫描及成功发布、生产构建下 Chrome 骨架可交互 p95、画布交互保持、后端全套检查、独立全范围复审。部分测试通过不等于 AC1–AC9 全部完成。

### 续作检查点（2026-09-14，仍非最终验收）

- 最新 0045 摘要 `55b4ed1993d2e50f7bec5dd18102185d439035fba3944b5c435ea61080984bd1` 已重跑隔离验证：升降重升；running、failed preview、未满足 demand、pending/failed delivery 拒绝破坏性回退；调用方外键开关 ON/OFF 均保持；3 用户/2 条事实保留、45 个输入触发器、receipt/overlay/intent lease schema 校验通过。
- 新增独立生产者测试 `test_steward_input_versions.py` 14 passed；无事件资料/披露/ref/账号/词条变更、跨桥接传播、纯时间到期、no-op/登录计数/实际发布不自失效、推测层重新授权通过。
- 中间冻结代码的诊断：朱氏 30 人/30 账号冷 13.5346s、热 0.1334s，冷写锁 p99 3.136ms/max 36.049ms，隐式写窗口 max 48.715ms，在线写入零错误；稀疏 50 人/50 账号冷 12.7531s、热 0.1706s，检查通过。200 人和真实扫描窗口仍需完成。
- 真实 Chrome 揭示重复 HTTP Date 导致前端安全拒绝内容，已通过专用 Validated-At 时间头修复。修复后单次桌面骨架 373.6ms、375px modern 719.1ms；两者称谓仍 pending 时已可浏览，平移/缩放在下一次称谓更新后保留。5 次 p95 与其他交互仍待最终验收。
- 完整前端 69 文件/646 个断言通过，但一次 Naive UI 消息计时器在测试环境销毁后抛错使退出码为 1，不算质量门禁通过；正在修复具体测试资源清理。旧 notifications XHR stderr 单独记录，不用屏蔽错误充当通过。

### 最终审查修复（2026-09-14，待最后后端与性能核验）

- 前端计时器清理已修复；补充 preparing/input_changed 安全空态的坐标、树布局锚点保留，以及空态期间撤权/切换主体清理。最终前端 69 文件 / 656 tests、lint、type-check、build 全部 exit 0；既有 notifications XHR stderr 未屏蔽。
- 四个公共消费者已统一读取 `current_view_payload`：亲属推荐、空间统计、household 元信息与称谓呈现。去掉首次 GET 同步物化；发布前不消费预览，发布后不再被旧 live 表的 queued 状态挡住。canonical helper 使用真实一致快照，保留调用方既有事务；称谓来源只内部传递，wire schema 不扩展。相关 29 项测试通过，独立审查探针确认 publication/preview 隔离和撤权隐藏。
- GC 候选发现移到读事务，短写阶段按候选 ID 重验 publication/共享结果/交付根后分批删除。补充必要引用索引，避免每轮自动建反向索引和全空间 MAX 聚合。
- 单 finding 通知交付按 8 名收件人拆分，后续批次补齐通知并保留已读/忽略状态。candidate 投影改为相关存在性查询；suggestions 增候选索引，inferred 复用已有索引。
- 独立分批失效探针：17 人的 8+8+1 批次中只完成首批，撤销末位成员后旧剩余批失效；下一代沿用 finding occurrence 但仍独立准备收件人批次，最终通知覆盖全部 16 位合法成员，无重复；领域事件/receipt/建议各仅 1 条。finding receipt 只去重领域事件，不能用于跳过通知批次。
- 已满足或已撤销资格的历史 demand 不再造成无限后继作业；恢复资格的新请求递增 revision。同水位晚到的合法需求继续保留。发布失败回归同步断言 fulfilled_revision 与指针、水位一起回滚。
- 剩余审查项：确认普通 delivery 失败预算不会被无变化扫描重置，补独立单项交付重试而不重算 core；最终冻结后重跑后端、0045 迁移、30/50/200 与真实两次 300 秒扫描、真实 Chrome 五次及 API smoke。
- `test_steward_runtime_recovery.py` 两项集成回归通过：真实单 spawn CPU 的大空间切片让出后，小空间经正常调度先发布；故障注入使一个 target 已提交、下一个只推进一步后丢失执行状态，经独立 Session/reaper/新 owner 接管，完成 pair 零搜索、半目标从头重做、旧回执/续租拒绝、预算保留原崩溃次数，最终只发布一次并提交原消费水位。
- 浏览器扩展脚本已完成实际节点拖动、选择结构关系和后续称谓更新保持的诊断验证；诊断采用中间冻结后端，不冒充最终源码性能。已有中间 200 人结果为冷 418.9668s / 热 0.8036s，显式写锁 max 251.245ms，隐式窗口上界 max 363.049ms，零请求错误；最终仍需新冻结源码复验。
- 集成检查发现 main 已新增 `0044_steward_terminology`，而任务分支已有 `0044_steward_generations` → `0045_steward_staged_publication`。本轮未获合并/部署授权，不改 main 或其他迁移；后续串行集成必须显式处理迁移分支，不可直接假定单 head 可部署。

### 最终门禁检查点（2026-09-14 09:43 UTC）

- 普通 delivery 跨代 effect 预算、持久领取租约、管理员单项 CAS 重试和 GC 接责/源失效收敛已完成，独立复核无阻塞；后继未发布时不得释放旧有效交付责任。管理端新增分页元数据列表与单项重试，模型 unknown 状态保持原恢复合同。
- 最终后端 `pytest -q -rs`：1144 passed、3 skipped、4 warnings，61.95s，exit 0。三项 skip 为既有延期 break-glass 家庭数据能力；四项 warning 为既有 SQLAlchemy/Python 3.12 datetime adapter 弃用提示。Ruff check、350 文件 format、mypy 195 源文件全部通过。
- 全量检查补齐了旧测试落点：输入生产者 fixture 显式开启 worker 后仍严格断言 stale/input_changed 和安全空态；日志脱敏在真实 run_slice/建议交付边界注入故障；管理员新路由白名单及跨 listener 隔离；计算版本断言更新为 pfv-v4。
- 全量 formatter 揭示任务基点的 0041/0042 换行差异，已仅在任务 worktree 整理，并逐文件证明 AST 与 HEAD 一致；迁移 SQL、种子和版本逻辑未改。主检出用户已有 0041 dirty 保留。该纯格式整理是 §2/§9 不改历史迁移业务逻辑边界内的质量修正。
- 最终 0045 SHA256：`d4be1434ac4a18cd2c95e2bb76c42406af492b9c0ada0dffb5ba1d79638e69c0`。隔离升降重升、所有拒绝破坏性回滚条件、FK ON/OFF、3 用户/2 真源事实、45 触发器以及 effect 指纹索引验证通过，报告 `/private/tmp/steward-migration-0045-final.json`。
- 当前验收源码 SHA256：`ad99524ed1f0c4db2eddbbd5a87cb43b3acfe4dfc6e78f837500e250e709b98c`（523 个运行时/构建/脚本文件）。前端 656 tests/build 的冻结版本保持一致。
- 最终性能脚本已启动：原朱氏 30 人/30 账号 + 稀疏 50/200，真实两次 300 秒扫描；写入 `/private/tmp/steward-final-recompute.json`。此处尚未取得扫描/200 人、最终 Chrome 五次和 API smoke 结果，AC1/AC3/AC9 不提前标通过。

### 长尾与计时复核（2026-09-14）

- 首轮 30/50 及 610.008 秒真实扫描通过，周期为 300.946 / 301.059 秒，3 个扫描作业均 published，在线请求零失败。200 人冷 386.1716s / 热 0.8882s，p99 13.147ms，但记录到显式窗口 524.496ms，严格门禁失败；完整报告保留为 `research/benchmark-final-first-attempt.json`。
- 发现量具 COMMIT 终点在统计回调后读取，可能计入提交后的应用等待。独立双连接复现：第二 writer 已在 0.23ms 内完成，统计等待 600ms 仍被旧量具报成 603.373ms 持锁。该证据证明量具有误计边界，不能反推原 524.496ms 的成因。
- 修正量具先固定 DB-API 返回时间，再记录统计；新增同事务 hold/commit/begin wait/thread CPU/安全符号追踪。v2 的 200 人复测冷 395.9825s / 热 0.9446s，显式 p99 10.96ms/max 488.387ms，隐式窗口上界 max 367.557ms，零请求失败；保存为 `research/benchmark-measurement-v2-200.json`。
- 488.387ms 样本位于 save_target；同事务 thread CPU 9.891ms、commit 1.29ms、10 条语句合计 470.344ms，最长 UPDATE 434.096ms。不同样本的各项 max 不相减，尚无 WAL/fsync 唯一归因。继续移除已确认的锁内重复工作：保存单目标时整行加载/解码骨架，以及预先编码检查大小后在 JSON bind 再编码。
- 独立量具审查同时补齐 SQL 异常自动 rollback 的结束计时、固定 verb 白名单；用真实 SQLite authorizer 和可控时钟验证 commit 耗时包含、统计耗时排除及失败事务继续计时。此检查点仍不代表最终 AC1 通过，需新冻结代码重测。

### 冻结复验检查点（2026-09-14 10:46 UTC）

- 保存单 target 改为锁外 JSON 编码与字节预算检查，短事务使用窄列读取和 Text 绑定；pending CAS、预算退款和进度仍原子且幂等。found/no_path 回归覆盖真实 BEGIN 到 commit，独立复审无新增阻塞。
- 最终后端：1153 passed、3 项既有 skip、4 项既有 datetime 弃用 warning，66.90s，exit 0。Ruff lint/format（351 文件）、mypy（195 源文件）、两性能脚本 lint/format、diff 检查均通过。量具 7 项确定性回归已纳入全套。
- v3 冻结摘要：`6180be62d335b8d46f8da9dd5ec247a70a0693a18257dad564f1d3c43fc5d1b2`（523 个运行时/构建/脚本文件），0045 摘要不变。200 人最终实测已启动；30/50 与两次真实扫描、Chrome 和 API smoke 尚未完成，不提前判定 AC1/3/9。

## 11. 最终本地验收（2026-09-14；主线接合前）

详见 [完整验收与原始证据](research/final-acceptance.md)。

- 后端 1153 passed / 3 既有 skipped / 4 既有 warnings，66.90s、exit 0；Ruff lint/format 351 文件、mypy 195 源文件通过。家庭前端 69 files / 656 tests，12.74s，lint/type-check/build 全通过且源文件未变。
- 30/50/200 冷算 14.8350 / 13.4756 / 327.4285 秒，热算 0.2157 / 0.2284 / 0.9224 秒，热算均零路径搜索和视图重建。冷显式写锁 p99 为 4.432 / 3.720 / 8.455ms，max 为 33.393 / 58.428 / 123.143ms；所有阶段隐式窗口上界 max≤237.128ms。并发请求零失败。
- 610.006 秒、122 次 maintenance tick，两真实扫描间隔 300.991 / 301.014 秒，3 个扫描作业均 succeeded；扫描显式 p99 4.539ms/max 109.732ms，隐式上界 max 43.437ms。未调大 busy_timeout 或扫描间隔。
- Chrome 桌面 5 次 p95 378.9ms；375px modern/paper 单次 361.3 / 346.7ms。首骨架出现时 29 个称谓仍 pending，后续更新保持平移/缩放/拖动/关系选择；三张截图已人工检查，无页面横向溢出。
- API smoke 30/30 通过。定位并修复其固定 internal:8001 与已有 SSH 隧道冲突：只在 smoke 配置新增随机 loopback internal 端口；该脚本 Ruff/diff 通过。首次 blocked 不是通过证据，最终结果单独保存。
- 性能/浏览器冻结摘要 6180be62d335b8d46f8da9dd5ec247a70a0693a18257dad564f1d3c43fc5d1b2；最终摘要 98be2abbe218db544e5923485abf39176ff1e05c8ecdd5b0b776573c34789989。523 文件比对仅 smoke 脚本不同，后端/前端/性能脚本未改；0045 迁移摘要仍 d4be1434ac4a18cd2c95e2bb76c42406af492b9c0ada0dffb5ba1d79638e69c0。
- 本地 AC1–AC9 的测试和性能证据齐全；独立最终审查无阻塞，记录见 research/final-review.md。热算 maintenance 单样本 667.559ms 为取锁等待；进程丢失恢复为受控故障注入；M4 本机结果不替代目标部署环境。
- 三组本地提交已备妥，待 Trellis §3.4 一次确认。main 存在 0044 迁移分支冲突，后续需串行集成；未推送、合并、部署或归档。分支未合并且续作代码未提交，按 AGENTS.md 保留 worktree，不执行强制清理。

## 12. 用户授权归档后的串行集成

用户“归档”“继续”授权完成本地提交、串行集成、验证、归档和清理。三组工作已提交为 `f7fbd387`、`6162cede`、`dddc6b77`；`ce085fb` / `3cb4550` 保留主线自动称谓、Memory/RAG 和管理员有效开关行为。0048 接合迁移分支并恢复特定升级次序丢失的 inferred triggers，两种升级次序及受限回退的数据保留已验证。

接合后的两次 200 人实测曾因隐式写入窗口 561.596ms、678.340/882.660ms 判失败，原报告完整保留。`84660c8` 移除重复 demand 写入和多余 JSON 工作；`c021921` 增加同进程/Engine 共享 FIFO 写入预算，累计 50ms writer 步骤后留出 100ms 共同空档，心跳入事务后重取时间。独立 SQLite 对照证明连续短事务可能反复抢在等待者前面；没有把这些等待唯一归因于 fsync。代码审查见 [集成复核](research/integration-review.md)。

最终代码 `dee91a14e85ca69d4c39044c1da359ea46700612` 包含主线 `be54c8d`；相对 `c021921` 只有 Trellis 文档变化。612 个运行时/构建/量具文件与冻结摘要 `fcfce00197a670412114968376836b50662b567d86aaeeb2a60e0782c373c5fd` 完全一致。

- 后端 **1535 passed / 3 既有 skipped / 4 既有 warnings**，133.67s，exit 0；Ruff lint/format 388 文件、mypy 204 文件通过。家庭前端 **740 tests**、管理员前端 **111 tests** 及各自 lint/type-check/build 全通过，构建字节未变。
- 30/50/200 人冷算 **17.7736 / 27.3105 / 416.1283s**，热算 **1.6526 / 2.4009 / 7.3182s**；热算均零路径搜索和视图重建。全部并发请求零失败，最终全部显式写锁 p99 ≤25.585ms，max≤241.481ms；含取锁等待的隐式窗口 max≤337.852ms。后台全空间墙钟耗时不等于持锁或本人等待时间。
- 610.006s 观测、121 次真实 maintenance tick；扫描在 0.315/300.928/602.958s 触发，间隔 **300.613/302.030s**，三个作业均 succeeded/published。busy_timeout=5000、扫描间隔 300 秒保持。
- 生产构建下 Chrome 桌面 5 次骨架可交互 p95 **389.3ms**；375px modern/paper 单次 **359.9/365.1ms**。30 人骨架出现时 29 个称谓仍 pending，后续称谓更新保留平移、缩放、拖动和选择；截图已人工查看，页面无横向溢出。
- 最终真实 API smoke **56/56 passed**，三个 listener 使用隔离 loopback 端口。完整证据及请求 p95/p99/max 见 [集成验收](research/integration-acceptance.md)。

本地 AC1–AC9 验收通过。共享写入预算不是跨进程调度器，单笔事务不可抢占；M4 本机性能不替代目标部署环境。稠密图仅有资源/语义回归，未采集连续 CPU/RSS 或操作系统真实重启证据。推送、生产备份和部署未执行。本地合并、归档及清理结果记录在集成验收末节。
