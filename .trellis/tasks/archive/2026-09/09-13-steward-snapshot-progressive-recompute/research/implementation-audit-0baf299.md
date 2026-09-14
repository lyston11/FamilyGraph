# 0baf299 实施续作审计

2026-09-14，基于任务 worktree 的干净 HEAD 0baf299。以下行号固定对应该提交，后续实现可能移动。三份独立只读审查与主线程关键路径抽查相互印证；此前 1061 passed 属于交接报告，不能替代新合同验证。

## 必须补齐的行为

- `steward.py:1392` 的 `_rebuild_space_derived` 直接写 live DerivedFact；`personal_family_view.py:199` 的 `rebuild_view` 在写入/删除后搜索路径，到 `:304` 置 current 再按 viewer 提交。generation 尚未隔离实际内容。
- `steward.py:1197` 的 `_execute_locked` 在 publish 前写 findings、suggestion、inferred、cards；`:1253` 前还登记 assist。模型 HTTP 有 succeeded 门禁（`steward_assist.py:533,696`），不能误报“模型已在发布前调用”。
- `_publish_success`（`steward.py:966`）只检查部分租约，无 expected attempt、输入版本、generation 和必需清单；`mark_failed`（`steward_generations.py:175`）按空间影响所有 running 代次，迟到失败可伤及新作业。
- `test_steward_generations.py:156` 固化“首次必需 PFV 失败仍结算”这一错误行为。任何未成功必需项都应阻止 publish/cursor；预算必须在计算前查验。
- `personal_family_view.py:1053` 的 demand 未绑定持久 account，focus 仅日志；`steward.py:1013` 只有更高 cursor 才登记后继，同水位需求缺乏保障。
- `derived_facts.compute_pair` 先搜索再取缓存；空间结构指纹匹配还会跳过 PFV 展示更新。词典变化必须重做展示并复用结构。
- 前端 `api/personalFamilyView.ts:475` 丢弃 304 头；store 无同空间请求/版本防倒退；403/404 不清快照。`FamilyTreeView.vue:370` 按顶层状态轮询，可在 running + ready + next_poll_ms=0 时忙循环，且卸载后回调能重启定时器。拖动位置/选中面板未从展示对象分离。

## 输入版本生产者清单

触发器同事务推进版本，领域事件只负责调度。UPDATE 用实际值变化 `OLD.column IS NOT NEW.column`；不能因写入未变值制造重算。结构/展示/推测分层，发布只查固定版本行，原始 hash 在锁外快照构建。

| 表 | 必需列 | 范围 |
| --- | --- | --- |
| source_facts | id, space_id, subject_user_id, object_user_id, fact_type, state, revision | OLD/NEW 空间及 active bridge 对侧；NULL global |
| space_members | space_id, user_id, status, role | OLD/NEW 空间及 bridge 对侧 |
| space_profile_refs | space_id, user_id, status | OLD/NEW 空间及 bridge 对侧 |
| family_spaces | id, kind, owner_id | 空间及 bridge 对侧；防删除重建 ABA |
| users | id, name, gender, birth, death, bio, avatar_path, privacy_mode, created_by, created_at, deleted_at, profile_status | 先保守 global；按结构/展示实际用途分层 |
| accounts | id, user_id, status, token_version, pin_must_change | global；排除登录失败计数、locked_until、pin_hash 等 |
| platform_role_assignments | account_id, role | global，家庭可见性 fail-closed |
| disclosure_preferences | profile_id, category, scope, space_id, allowed | global 或 OLD/NEW 空间 |
| personal_family_bridges | id, 两端 space/anchor, status, revision, expires_at, scope_json, consent accounts | OLD/NEW 两端 |
| term_entries | id, concept_code, level, space_id, owner_account_id, locale, term, status, revision | 展示版本；空间或保守 global |
| agent_space_provider_settings | space_id, agent_kind, inferred_tree | 推测版本 |
| steward_inferred_edges | id, space_id, subject/object, relation_kind, status, revision, source_candidate_id, evidence_hash/json, created_at | 推测版本；不能触发 confirmed 无限重算 |
| steward_llm_candidates | id, space_id, status, payload_json | 候选→推测准备阶段才需要 |

出处：`relationship_graph.py:169,287,309,331,433`，`visibility.py:124,173,198,240,270,284,307,347`，`terms.py:125,165`，`steward_inferred.py:55,111,180,249`。推测设置 API（`:229,270`）与种子词典写入不保证发领域事件，service-only hook 不完整。

时间边界至少取有效 bridge 最近到期与下一 UTC 日期边界；后者保守覆盖成年权限改变。代码/config 版本另入 fence。scope revision 不随 scope 删除归零。

## 测量口径修正

旧 benchmark 创建树外 owner，只有 owner 有 Account，未代表大家族真实 PFV 工作量；旧并发 probe 的 max 为客户端写请求总时长，不是直接测得的持锁时长。WAL autocheckpoint/fsync 尚无因果证据。

新脚本使用树内 viewer，默认全人物有 Account；复用现有朱氏 30 人六代事实，另造稀疏 50/200 人。DBAPI 记录 BEGIN IMMEDIATE 等待、拿到锁后至 commit/rollback 返回的持锁、commit 时间；隐式写窗口单列为包含竞争等待的上界。在线 ASGI 登录、lease、PFV 请求和独立 maintenance 同时运行。真实 300 秒扫描另报，不把模拟时钟当真实窗口。

本机 ASGI 服务端/客户端时间不能冒充实际浏览器渲染或目标部署网络耗时；最终报告须保留环境与样本数量。

### 已复现的冻结旧版基线

从 `0baf299` 导出独立临时源码，运行校准过的新测量脚本。30 人、30 个账号、45 条既有朱氏关系；macOS 26.2 arm64、Python 3.12.12、SQLite 3.51.1、WAL、busy_timeout=5000、synchronous=NORMAL、wal_autocheckpoint=1000、bcrypt rounds=12。完整原始数据见 [基线报告](benchmark-baseline-0baf299-ming30.json)。

| 观测 | 结果 |
| --- | --- |
| 冷 / 热重算 | 51.9462s / 0.0345s |
| 首次确认骨架 / 首批称谓 | 均为 24477.4ms，没有骨架先出的阶段 |
| 登录错误 | 3 次 HTTP 500 |
| lease / maintenance 错误 | 3 / 2 次 OperationalError |
| 显式写事务持锁 p99 / max | 46.514 / 67.171ms |
| 隐式写事务持锁**下界** p99 / max | 1003.181 / 1003.181ms |
| 隐式写窗口**上界** p99 / max | 5199.835 / 5199.835ms |
| BEGIN IMMEDIATE 等待 p99 / max | 1908.874 / 4828.684ms |
| commit p99 / max | 7.374 / 9.810ms |
| 登录客户端 p99 / max | 5477.915 / 5477.915ms |

下界从首次 DML 返回后计到 commit/rollback 返回，排除了取锁等待，仍超过 500ms。该证据证明旧 PFV 计算存在长持锁；本次 commit 时间并不支持把主因归到 checkpoint/fsync。上界包含等待，不能表述为“持锁 5.2 秒”。显式/隐式事务必须一并验收，不能只看显式 BEGIN 的漂亮分位数。

测量器另以两个独立 SQLite 连接校准：首连接持锁约 55ms，第二连接取锁及首次 DML 约 63ms，实际持锁下界约 35ms，总窗口约 98ms；两次提交各记一次。此基线是旧代码证据，不代表正在实现的修复已通过。
