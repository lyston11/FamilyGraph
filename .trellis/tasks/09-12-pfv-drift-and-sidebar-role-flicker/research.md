# 排查证据：家族树空视图死锁与侧栏入口闪烁

排查日期：2026-09-12。环境：远程开发模式（`dev-up-remote.sh`），后端/数据库在 lyston 服务器（ubuntu@161.153.127.30），systemd user unit `familygraph-api`（三 listener 8000/8001/8002），env 文件 `/home/ubuntu/.config/familygraph/familygraph.env`。

## 1. 现象与结论速览

| # | 现象 | 结论 |
|---|------|------|
| 1 | 家族树「0 位家人」、无报错；`GET /api/personal-family-view` 200 | 视图版本漂移 → GET 返回安全空 payload；失效→入队→重建链条双重死锁 |
| 2 | 侧栏「空间管理」间歇消失 | `loadMembers` 先清空 members 再请求 + 失败被 `.catch(() => undefined)` 吞掉 |

## 2. 排查时间线（服务器 UTC）

- 09-12 05:02 服务器 DB 出现新 bootstrap 管理员凭据文件（说明该库此前经历过一次无管理员的重建/重置）。
- 09-12 12:16:52 `familygraph-api` 进程启动（pid 222437）——**该进程代码早于 pfv-v2 提交**（后续由其重建结果反证：写出的 `computation_version='pfv-v1'`）。
- 09-12 12:31:43 / 13:01:34 `familygraph-code-sync.timer` 同步代码到 238b0bd，**但不重启服务**。
- 用户在 5174 管理后台用初始密码登录成功后，在 5173 报告两个缺陷（12:25-12:33 UTC 的访问日志可见相关请求）。
- ~12:43-44 UTC 人工将 2 行漂移视图标 `stale`；12:47:14 UTC 下一批 integrity_scan 作业（id 33-36，spaces 1-4 全部 succeeded）完成重建，视图恢复 `current`，space 2 视图 11 节点（12:47:17）。

## 3. 问题 1 代码证据（backend，本地 HEAD 238b0bd）

### 3.1 GET 空响应路径（fail-closed，行为本身正确）

- `backend/app/api/personal_family_view.py:39-72`：`read_personal_family_view` → `view_is_current` False 时 `request_view_recompute()` + `view_payload()`；而 `view_payload` → `_view_payload_for_view`（`backend/app/services/personal_family_view.py:400-407`）开头即判 `view_is_current`，False → `_safe_empty_payload`（:384-397）返回 **200 + `nodes: []` + `stale_reason`**。
- `view_is_current`（`personal_family_view.py:304-324`）：要求 `status=='current'` 且 `policy_version==POLICY_VERSION` 且 `computation_version==COMPUTATION_VERSION` 且 `input_hash` 与当前图/词典指纹一致。docstring 明示「旧 policy_version='graph'、缺词典版本指纹的视图一律判不新鲜」。

### 3.2 死锁 A：GET 侧重算入队被水位吸收

- `request_view_recompute`（`personal_family_view.py:594-619`）：`cause="domain_event"`、`trigger_cursor=current_event_watermark(session)`；且 `STEWARD_ENABLED=false` 时直接 return。
- `enqueue_steward_job`（`backend/app/services/steward.py:444-478`）幂等规则：**「已 succeeded 且 last_event_cursor ≥ 本次水位（非 admin_rerun）→ 幂等返回历史作业，重放零副作用」**。没有新领域事件 → 水位不前进 → GET 侧重算请求永远被吸收。

### 3.3 死锁 B：worker 重建筛选跳过「current 但版本落后」的行

- `rebuild_space_views`（`personal_family_view.py:622-652`）：筛选条件 `PersonalFamilyView.status.in_(("queued","stale","failed","never_computed"))`——只看**存储状态**，不比对版本字段。
- steward 空间作业处理器调用点：`backend/app/services/steward.py:1020-1022`（`stats["personal_family_views_rebuilt"] = rebuild_space_views(db, space_id=space.id)`）——即周期 integrity_scan 作业每 ~5 分钟跑一次，但对存储状态 `current` 的漂移行永远空转。
- 版本常量：`personal_family_view.py:45-46`（`COMPUTATION_VERSION="pfv-v2"`、`POLICY_VERSION=config.POLICY_VERSION`）；`backend/app/config.py:80`（`POLICY_VERSION="v2-foundation-1"`）。
- 领域事件失效路径（对照）：`invalidate_view_scopes`（`personal_family_view.py:505+`，调用点 `backend/app/services/domain_events.py:224-226`）在事件事务内标 `stale`——只有发生新事件才会解死锁，纯版本升级不触发。

### 3.4 DB 证据（修复前）

```
personal_family_views（修复前）:
1|1|current|graph|pfv-v1|...|2026-09-12 05:16:38   (space 1 王德海家)
2|1|current|graph|pfv-v1|...|2026-09-12 06:08:44   (space 2 王氏家族)

personal_family_view_nodes（view 2）: 11 行
  lineage_summary|confirmed_path|10 ; self_private|root|1
steward_jobs 尾部: 全部 integrity_scan / succeeded（无 domain_event 重算作业）
```

## 4. 问题 2 代码证据（frontend，本地 HEAD 238b0bd）

- 显示条件：`frontend/src/components/shell/AppShell.vue:93`（`canManageCurrentSpace`）、`:269-274`（侧栏入口 `v-if`）、`:320-321`（账号菜单同名入口）。
- 角色解析链：`frontend/src/stores/spaces.ts:49-75`（`currentMembership` → `currentRole` → `isSpaceAdmin` → `canManageSpace`）。
- **先清后取**：`spaces.ts:166-182` `loadMembers()` 第一步 `this.members = []`，随后 `await fetchSpaceMembers(spaceId)`；在途窗口内 role=null → 入口消失。
- 触发源：
  - 路由守卫 `frontend/src/router/index.ts:162-186`：每次进入 spaceManagerOnly 路由 `await spaces.loadMembers(targetSpaceId)`（失败 fail-closed 拒绝，正确）；但 `loadMembers` 内部 `this.currentSpaceId = spaceId`（spaces.ts:168）造成**当前空间被顺手切换**的副作用（R6）。
  - `frontend/src/composables/useSpaceContext.ts:150-194` `switchSpace`：`loadMembers(spaceId).catch(() => undefined)`（:177）——失败静默。
  - 视图挂载：`MemoryView.vue:18`、`DisclosureMatrix.vue:52`、`SpaceManagementView.vue:82`、`PersonProfileView.vue:110`。
- 服务器日志佐证（12:25:10-12:25:14 UTC，5 秒内）：`/api/spaces` ×4、`/api/spaces/1/members` ×4、`/api/spaces/1/ownership-transfers` ×4、`/api/spaces/1/profile-refs` ×4——重校验窗口频繁且可观测。
- 服务器（远程）延迟放大窗口期；本地全模式同样存在，只是窗口更短。

## 5. 已执行的临时恢复记录（2026-09-12 ~12:44 UTC）

```sql
-- 仅派生投影表；等价于系统在领域事件时的 invalidate_view_scopes 动作
BEGIN IMMEDIATE;
UPDATE personal_family_views SET status='stale', updated_at=datetime('now')
WHERE status='current'
  AND (computation_version != 'pfv-v2' OR policy_version != 'v2-foundation-1');
COMMIT;
-- changed_rows=2（两行均按「与运行中进程代码匹配的版本」判断；当时运行进程为 pfv-v1 代代码，
-- 故 WHERE 里与 pfv-v2 比对仍命中其 policy_version='graph' 的漂移）
```

结果：下一周期 integrity_scan（作业 id 33-36，spaces 1-4 全部 succeeded）重建成功：

```
personal_family_views（修复后）:
1|1|current|v2-foundation-1|pfv-v1|f29800fa81f8|2026-09-12 12:47:14   (6 nodes)
2|2|current|v2-foundation-1|pfv-v1|ab586d5286ef|2026-09-12 12:47:17   (11 nodes)
```

与运行中进程（pfv-v1 代代码）的 `view_is_current` 全部匹配 → 家族树恢复。用户刷新页面即可。

**复发推演**：服务器下次 `systemctl --user restart familygraph-api` 使 238b0bd（pfv-v2）生效时，存量 `pfv-v1` 视图再次漂移 → 死锁 A/B 再次成立 → 家族树再次空。本任务的 R1/R2 落地后该场景应自愈（PRD 验收第一条即模拟此场景）。

## 6. 与本任务相关的运维背景

- 服务器代码同步定时器**不重启服务**：`server-sync-code.sh` 仅拉代码。版本常量升级型提交需要显式重启，而重启正是触发本次视图漂移的入口——修复方案需覆盖「重启后存量视图自愈」。
- `STEWARD_ENABLED` 在服务器 env 中已开启（steward_jobs 持续创建/处理可证）；`PERSONAL_FAMILY_VIEW_ENABLED` 已开启（接口未 503）。
- 管理员初始凭据交付机制（`backend/app/services/admin_bootstrap.py`）与本地/远程两套 DB 并存导致「本地凭据文件对远程库无效」——已口头告知用户，不属本任务范围。
