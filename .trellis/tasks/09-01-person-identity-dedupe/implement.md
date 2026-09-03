# 人物重复建档防护与 Steward 回溯审计：实施计划

## 实施顺序

1. **固化基线回归（不改产品代码）**
   - 核对 `test_person_dedupe.py` 三类断言在当前主干通过；如有缺口（例如 `space_members` 作用域覆盖）先补测试再动实现。
   - 回滚点：纯测试提交。

2. **失效接入（`domain_events.py` + `delete_profile_core`）**
   - `_invalidate_personal_family_view` 前缀元组追加 `"profile."`；按 `payload.space_ids` 逐空间失效。
   - `delete_profile_core` 在删除前收集 active refs/members 空间写入 `profile.deleted` payload。
   - 测试：profile.created/updated/deleted/merged 事件触发对应空间 stale；不触碰 `space_member.` 等既有前缀（followup 领地）。
   - 回滚点：独立提交，可单独回退。

3. **合并命令核心**
   - `commands/members.py` 新增 `merge_duplicate_profile`（design §2.1 八步）；`services/source_facts.py` 补指向修订的事件合同路径。
   - `MergedProfile` 结果 dataclass（survivor/retired/moved 计数/already_merged）。
   - 测试：design §7 主路径 + 状态门 + 复核门 + 幂等。
   - 回滚点：命令尚未暴露 API，可单独回退。

4. **只读查询与 API 路由**
   - `GET /api/spaces/{space_id}/duplicate-people` 与 `POST .../merge` 路由 + schema；授权 deps 复用空间成员/custody 判定。
   - 测试：端点授权矩阵、防枚举 404、请求体校验、错误 envelope。
   - 回滚点：路由层独立。

5. **审计收敛与 Scenario E 回归**
   - 合并前后 `_detect_findings` 签名对比测试；`load_graph`/PersonalFamilyView 中 retired 消失、两树经 survivor 连接的领域测试。
   - 回滚点：纯测试。

6. **收尾**
   - 若实现中发现 §0.9 需要补充合并语义，走 `trellis-update-spec` 更新 architecture.md §0.9（新增"处置"小节），不扩写其他章节。

## 文件范围预期

后端：

- `backend/app/commands/members.py`（合并命令 + deleted payload）
- `backend/app/services/source_facts.py`（指向修订事件路径）
- `backend/app/services/domain_events.py`（前缀接入）
- `backend/app/api/spaces.py`、`backend/app/schemas/space.py`（查询/合并端点）
- `backend/app/errors.py`（如需新错误码；尽量复用既有码）
- `backend/tests/test_profile_merge.py`、`backend/tests/test_person_dedupe.py` 扩展

不触碰：前端（无 UI 变更）、迁移（无 schema 变更）、`steward.py` 的检测逻辑（口径不变）；仅修改 `schedule_steward_job_for_event` 的 profile 事件空间 fan-out，使 `profile.*` 只触达 payload.space_ids 指定的受影响空间。`space.membership` 失效前缀仍归 followup 领地。

## 验证命令

窄范围：

```bash
cd backend && .venv/bin/python -m pytest -q tests/test_profile_merge.py tests/test_person_dedupe.py tests/test_steward.py tests/test_personal_family_view.py
cd backend && .venv/bin/python -m mypy app/commands/members.py app/services/source_facts.py app/services/domain_events.py
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

全范围：

```bash
cd backend && .venv/bin/python -m pytest -q --deselect tests/test_ownership_transfer.py::test_concurrent_double_accept_single_winner
cd backend && .venv/bin/python -m mypy app
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## 安全回归

- 合并不得让操作者借道获取 survivor/retired 之外任何档案的字段（只读端点字段白名单）。
- claimed 档案在任何路径下都不可被合并（防绕过 claim_dispute）。
- 删除/合并后读取路径不得返回 retired 人物（含 lineage_summary 桥接投影）。
- 并发合并与并发建档互不破坏 `BEGIN IMMEDIATE` 语义。

## 最终开始前检查点

- [ ] PRD/design/implement 三件套齐备且相互一致。
- [ ] `task.py validate 09-01-person-identity-dedupe` 通过，manifest 非 seed-only。
- [ ] 与 `09-02-personal-family-view-followup` 的 `domain_events.py` 交叉改动已协调（先后合入顺序明确）。
- [ ] 用户在最终规划摘要后明确批准，才执行 `task.py start`。
