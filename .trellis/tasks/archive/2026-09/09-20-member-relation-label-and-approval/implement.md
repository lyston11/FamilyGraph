# 实施计划

## 规划与启动

- [x] 核对现状：房主（唯一 active `space_admin`）已存在；退出已无需审批且即时清权；关系事实无用户写入口；自由关系词表不存在。
- [x] 用户确认：审批链选 (a)；关系词自由文本不限词表；「双方可改」指关系词本身；显示范围为「家族树画线 + 个人页」。
- [ ] 用户批准规划后 `task.py start`，进入隔离 worktree。

## 实现顺序

1. 迁移 0053：`space_members` 加 `origin`/`owner_approved_at`（可空）；新建 `member_relation_labels`（唯一无序对 + 长度 CHECK）。
2. `space_fsm`：`invite`/`invite` 复活时写 `origin`；`transition('accept')` 按 `origin` + `owner_approved_at` 判定（未批准 → 403；invite 已批准仅受邀人本人；join_request/code 由批准动作内部完成）。
3. 新增 `approve_membership` 命令 + `POST /space-memberships/{id}/approve`（房主批准；`added_by == actor_id` 时拒绝自批）。
4. 三条链写关系词：`invite_into_family_household`、`request_join_by_user`、`redeem_invite_code` 各增必填 `relation_label`，写入标注行。
5. 修改/清空：`PUT /spaces/{space_id}/member-relation-label`（仅该对两端本人）。
6. 读取投影：`member_relation_labels_for` + 接入 `steward_views.payload_for` 与 `personal_family_view._view_payload_for_view`；PFV 载荷与 schema 增 `label_edges`。
7. 前端：`label_edges` 独立标注层渲染（家族树）+ 个人页显示与修改入口；三条加入表单加「与对方的关系」必填输入；成员列表区分「待房主批准 / 待受邀人接受」。
8. 回归测试：
   - 后端 `tests/test_member_approval_and_labels.py`：三条链的顺序与自批拒绝、关系词必填/长度、双方可改与第三方不可改、标注边不进亲属图（填「朋友」后无亲属边）、任一端退出后边消失。
   - 前端：家族树标注边渲染、个人页显示/修改、三条表单必填校验。
9. 更新 spec `architecture/4--4-ad-4.md`（审批链 + 标注边合同）。

## 验证

- `cd backend && .venv/bin/python -m pytest -q tests/test_member_approval_and_labels.py tests/test_family_space_join_invite.py tests/test_space_leave.py tests/test_invite_codes_service.py tests/test_invite_codes_api.py tests/test_space_invite_authz.py tests/test_m2c_flows.py`
- `cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app`
- `cd frontend && npm run lint && npm run type-check && npm test && npm run build`
- `./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json`（端点与载荷变更）。
- 隔离库演练迁移（upgrade/downgrade），确认旧 pending 行语义不变。

## 回滚点

先回退前端入口与 `label_edges` 读取字段，再回退命令与迁移 0053；旧行 `origin IS NULL` 全程保持旧语义，不需要数据修复。
