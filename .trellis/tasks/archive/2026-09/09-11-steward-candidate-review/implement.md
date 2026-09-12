# Implement — Steward 候选审核、冲突待办与站内通知闭环

## 开始条件

- [x] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [x] 确认依赖：`09-11-steward-production-ops`, `09-11-steward-assist-execution`, `09-11-steward-projection-consistency`, `09-11-steward-quality-security`。
- [x] 取得最新实施摘要的明确批准并完成本任务实现；发布门禁仍为 partial。

## 有序执行

- [x] 1. 列出 SourceFact 全部 kind 到可执行用户命令的映射，先写 owner 非当事人不可确认回归。
- [x] 2. 实现 Suggestion/Recipient 与必要 typed proposal 模型、迁移、schema、证据校验和唯一键。
- [x] 3. 实现从安全模型候选和确定性 findings 投影；复用身份证据/terms/source_facts 服务，禁止原文直出。
- [x] 4. 实现 scoped list/dismiss/submit + 幂等领域命令；补关系方向/双当事人确认与撤权测试。
- [x] 5. 扩展现有 notifications 生成/投影/引用，补实际 NoticeItemRow 导航和 read-only 语义。
- [x] 6. 实现 decoder/store/建议详情确认 UI；联测 submitted→领域决议→Steward 重算→resolved。

## 改动边界与重用位置

- `backend/app/services/steward_assist.py`
- `backend/app/services/steward.py`
- `backend/app/services/notifications.py`
- `backend/app/models/notification.py`
- `backend/app/commands/connections.py`
- `backend/app/services/source_facts.py`
- `backend/app/services/terms.py`
- `frontend/src/views/NotificationsView.vue`
- `frontend/src/components/notifications/NoticeItemRow.vue`
- `frontend/src/stores/notifications.ts`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_notifications.py tests/test_source_facts.py tests/test_action_cards_api.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [x] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [x] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [x] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
