# Notes — Steward 个人视图、称谓与失效传播修复

## 2026-09-11 规划记录

- F09/F11 静态代码证据明确，实际浏览器表现以回归复现为验收，不能借已归档任务断言无缺陷。
- F10 read 过滤两端但回传原 path_json，安全测试必须包含隐藏中间节点与替代路径。
- 当前无码自注册已存在（09-05），不再称其为“未来功能”；本任务只补其合法访问后的派生初始化，不启用全平台找人。
- 已确认 `backend/tests/test_terms.py` 存在；实施时沿用并补 PFV 集成用例。

## 决策与证据边界

- 已确认路线：确定性核心 + 可选候选/排序/解释；先可靠性再用户审核；站内通知。
- 对应条目：[F09](../09-11-steward-complete-hardening/research/findings.md#f09), [F10](../09-11-steward-complete-hardening/research/findings.md#f10), [F11](../09-11-steward-complete-hardening/research/findings.md#f11), [F12](../09-11-steward-complete-hardening/research/findings.md#f12), [F13](../09-11-steward-complete-hardening/research/findings.md#f13)。
- 当前状态：规划完成待实施评审；本轮不启动。实现清单全部未勾选，不代表工作已完成。
- 09-09 会话报告核心/维护 46 passed、辅助 16 passed；这次没有重复运行测试，不能作为未来改动通过依据。

## 实施与验证约定

本轮仅生成规划，未修改上述产品代码，也未重跑历史测试。文件行号以 2026-09-11 工作区为准；实施前必须读将修改的完整函数与现行 spec。
迁移必须接实施时唯一 head，不硬编码已被并行工作使用的编号；测试只用隔离 DATA_DIR。不得把 conftest 的 downgrade base 对准业务库。

## 实施结果待记录

实施后逐 AC 追加实际命令、退出码、必要的脱敏证据及剩余问题。本节是交接记录入口，不替代 PRD 验收。

## 2026-09-11 实施记录（已执行，未提交）

### AC 逐条结果

- AC-1（R1 统一事件影响）：`backend/app/services/domain_events.py` 重写为
  `resolve_pfv_impact`（space.* / space_profile_ref.* / relation.* / term.* /
  disclosure.* / profile.* / personal_family_bridge.*，全局人物事件按该人物
  active membership ∪ active SpaceProfileRef ∪ active 桥接关联空间收敛，
  `term.personal_updated` 只命中本人账号视图，memory./rag. 永不触发空间失效或
  Steward 工作）。矩阵测试 5 条全过：
  `space.membership.changed` 只废本空间、`term.personal_updated` 只废本人账号、
  全局 `source_fact.revoked`（真实 producer `transition_source_fact`）只废成员
  所在空间、`disclosure.updated`（真实 producer payload 形状）同上、
  `memory.confirmed` 零视图失效零 StewardJob。
- AC-2（R2 初始化收敛）：`initialize_account_views`（queued/never_computed 唯一
  行，仅 active 成员且持有 Account 的目标；无空间不建行、provisional 无 Account
  不伪造）由注册命令（`commands/registration.py`，事务内显式调用）与成员资格
  获得事件（`space.created` owner / `space.membership.changed`
  action∈{accepted, household_link_activated}，`domain_events._maybe_initialize_views`）
  触发。测试：无码注册零视图行、stranger 码注册建 queued 行、household 码加入建
  queued 行、后加入成员（invite_member+respond_invitation 真实命令）建 queued 行、
  首路径形成后 `rebuild_space_views` 到达 current、provisional-only 人物无视图行。
  GET 全程只读（`get_current_view`），无浏览器 GET 不触发任何计算。
- AC-3（R3 路径重验）：`_view_payload_for_view` 先做 `view_is_current`（status +
  policy/computation 版本 + 当前图/词典 input_hash 复算），不新鲜一律安全空内容；
  current 时逐边重验 `_path_evidence_valid`（每步事实存在/confirmed/空间适用/
  端点与方向一致，路径上所有节点含中间人当前可见），主路径失效整边不输出、替代
  路径逐条剔除。测试：viewer→两个中间人→祖辈双路径，撤销中间事实后旧主/替代路径
  均不输出（事件同步标 stale + 模拟失效遗漏强制 current 仍拒绝）、推荐 confirmed
  类目为空、重算后仅剩未撤销事实的路径。
- AC-4（R4 称谓与版本）：`rebuild_view` 接入 `terms.resolve_term_or_structural`
  （personal>space>locale>system，结构描述仅兜底；不触碰 SourceFact/
  raw_relation_inputs）；`policy_version` 存 `config.POLICY_VERSION`（v2-foundation-1），
  不再写 purpose="graph"；`input_hash` 加入四级词典版本指纹
  `_term_registry_hash`。测试：同空间两账号不同个人称谓互不覆盖、改词触发
  status/version/input_hash/ETag 变化。
- AC-5（R5 条件缓存与持久化）：`etag_for(view, account)` 绑定
  token_version+view_version+status+input_hash+policy/computation 版本；API 先
  `get_current_view`（404 授权判断先于缓存判断）+ `view_is_current` 才允许 304，
  旧 If-None-Match 在撤权/策略版本变化/改称谓/bridge 过期后全部 200+安全空内容；
  首读物化仅保留在 space_stats/household_card（显式 commit），跨独立 SessionLocal
  持久读取有测试；GET 重算登记走 `request_view_recompute`（独立短事务 canonical
  enqueue，幂等，失败只记日志不破坏 GET）。

### 命令与退出码（backend 目录）

- `.venv/bin/python -m pytest -q tests/test_personal_family_view_consistency.py`
  → 20 passed（exit 0）
- 定向回归（pfv/bridge/terms/space_stats/auth/invite/members/maintenance/
  household_card/action_cards_core）→ 129 passed（exit 0）
- `tests/test_steward.py tests/test_steward_assist.py tests/test_admin_steward.py`
  → 94 passed（exit 0）
- 全量 `.venv/bin/python -m pytest -q tests` → **912 passed, 3 skipped**（exit 0）
- `ruff check .`：本任务改动文件全部通过；仓库另有并行任务未提交文件的既有
  E501/I001（app/api/space_model_settings.py、app/api/spaces.py、0028 迁移、
  test_space_lineage_link/test_space_model_settings 等），不属于本任务、未改动。
- `ruff format --check`：本任务文件 clean；并行文件既有 reformat 提示同上。
- `mypy app` → 5 errors 全部位于未触碰的 `app/api/space_model_settings.py` 与
  `app/api/admin_agent.py`（并行任务基线），本任务文件 0 新增错误。

### 改动文件

- `backend/app/services/domain_events.py`：统一事件影响解析 + 成员获得时初始化。
- `backend/app/services/personal_family_view.py`：COMPUTATION_VERSION=pfv-v2、
  真实 policy_version、词典 term 解析与词典指纹、view_is_current 新鲜度、
  路径/替代路径重验、安全空 payload、invalidate_view_scopes（账号粒度）、
  initialize_account_views、request_view_recompute、rebuild_space_views
  每视图 SAVEPOINT（单视图失败不污染 Session）。
- `backend/app/api/personal_family_view.py`：GET 全只读、授权/新鲜度先于 304、
  显式短事务登记重算；响应体与错误 envelope 不变。
- `backend/app/commands/registration.py`：注册事务内后台初始化。
- `backend/app/services/household_card.py`、`space_stats.py`：首读物化收敛为
  显式「仅 never_computed 时 rebuild + commit」，stale/queued 行不再隐式重算。
- `backend/tests/test_personal_family_view_consistency.py`：新增 20 条回归。
- 未新增迁移（无 schema 变更），迁移 head 仍为 0037；未触碰
  frontend/src/api/personalFamilyView.ts（状态枚举/响应体未变，无需 decoder 同步）。

### 假设与剩余风险

- 全局人物事件的影响面按「该人物为 member/ref 的空间 ∪ 其 active 桥接对端空间」
  收敛，是该设计的精确实现而非数学最小集（可能少量过失效，不会漏失效）。
- `request_view_recompute` 依赖 STEWARD_ENABLED；关闭时 GET 仍返回安全空态，
  重算由后续合法事件/维护循环承担（与回滚顺序一致：辅助可关、授权复核不可回退）。
- 新事件类型 `space.membership.changed` 的 action 白名单
  {accepted, household_link_activated} 采自当前生产者采样；若未来新增授予动作，
  需同步 `_MEMBERSHIP_GAIN_ACTIONS`。
- 未获得外部证据：未做真实浏览器 E2E、未在真实业务库做迁移往返（本轮无迁移）。

## 2026-09-11 检查记录（trellis-check，未提交）

### 逐 AC 复核（读码 + 测试对照）

- AC-1：`domain_events.resolve_pfv_impact` 前缀矩阵与真实 producer payload 逐一
  对上——`source_fact.*` payload 带 subject/object_user_id（source_facts.py
  `_fact_payload`）、`term.personal_updated` 带 account_id（terms.py:340）、
  `term.space_promoted/demoted` 带 space_id、`disclosure.updated` global 分支靠
  aggregate_id=profile id 收敛、`personal_family_bridge.*` 带 space_ids、
  `relation.created` 带 from_user/to_user。memory./rag. 双重排除（PFV 前缀表 +
  `_schedule_steward_job` 早退）。矩阵测试 5 条核实通过。
- AC-2：注册事务内 `initialize_account_views`（registration.py:139）；
  `space.created`/`space.membership.changed` 增益动作经 `_maybe_initialize_views`；
  queued/never_computed 无计算内容；provisional 无 Account 不建行有测试。
- AC-3：`view_is_current`（status+policy+computation+graph/词典 input_hash 复算）
  是失效遗漏的兜底；`_path_evidence_valid` 逐步验事实/方向/端点/中间人可见性，
  主路径失效整边不输出、替代路径逐条剔除。隐藏中间人双路径测试核实通过。
- AC-4：`rebuild_view` 走 `resolve_term_or_structural`，不改 SourceFact；
  `policy_version=POLICY_VERSION`（config.py:80 v2-foundation-1），purpose="graph"
  全仓仅存注释；`_term_registry_hash` 四级词典指纹进 input_hash；两账号称谓隔离
  测试核实通过。
- AC-5：`etag_for` 绑定 token_version+view_version+status+input_hash+policy/
  computation 版本；API 404 授权判断先于 304，304 需 `view_is_current`；
  bridge 手工过期虽无事件，但 graph snapshot_hash 变化使新鲜度判定兜底拒绝 304
  （有测试）；household_card/space_stats 首读物化显式 `session.commit()`，跨
  `SessionLocal` 持久读取有测试；GET 重算登记走 `request_view_recompute`
  （独立 Session + canonical `enqueue_steward_job`，cause="domain_event" 在
  STEWARD_JOB_CAUSES 白名单内，异常仅告警日志）。
- 响应兼容：`PersonalFamilyViewOut` 未变（extra=forbid，stale_reason 已有字段），
  前端 decoder 无需同步。无新增迁移，head 仍 0037。

### 检查中发现并修复

1. `backend/app/services/personal_family_view.py:443-444`（原）——
   `_view_payload_for_view` 在读取序列化时写回 ORM `node.display_json`/
   `node.visibility_level`，违反 design「GET 不在序列化时改 ORM display_json」。
   已改为局部 dict 组装响应（display/level 仅进 payload，不落 ORM）。修复后
   全量 912 passed, 3 skipped；该文件 ruff check/format/mypy 均干净。

### 检查命令与退出码（backend 目录）

- `.venv/bin/python -m pytest -q` → 912 passed, 3 skipped（exit 0；修复前后各跑一次）
- `.venv/bin/ruff check <本任务 7 个文件>` → All checks passed（exit 0）
- `.venv/bin/ruff format --check <本任务 7 个文件>` → 7 files already formatted
- `.venv/bin/python -m mypy app` → 5 errors 全部在并行任务未提交文件
  （app/api/space_model_settings.py、app/api/admin_agent.py），本任务文件 0 新增。
- 仓库级 ruff 余留 7 处 E501/I001 也全在并行文件（0028 迁移、
  test_space_lineage_link、test_space_model_settings、spaces.py 排序等）；
  `ruff format` 对 app/commands/spaces.py 的 reformat 提示源自并行 lineage-link
  diff，不属于本任务，未代改。

### 检查结论

PASS。无 BLOCKER/MAJOR；1 处 MINOR（GET 序列化写 ORM）已当场修复并全量回归。
剩余风险沿用实施记录「假设与剩余风险」节，不阻塞验收。
