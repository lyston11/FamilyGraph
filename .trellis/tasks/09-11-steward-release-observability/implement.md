# Implement — Steward 端到端发布门禁与可观测性

## 开始条件

- [ ] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [ ] 确认依赖：`09-11-steward-production-ops`, `09-11-steward-assist-execution`, `09-11-steward-projection-consistency`, `09-11-steward-quality-security`, `09-11-steward-candidate-review`。
- [ ] 取得最新实施摘要的明确批准再 task.py start；当前仅规划。

## 有序执行

- [ ] 1. 确认实际 DATA_DIR、迁移 head、三 listener 与新 config 的模板透传；制作不输出秘密的状态检查。
- [ ] 2. 实现有界结构化指标和安全异常日志，复用 system-admin-frontend 状态入口。
- [ ] 3. 编写隔离 E2E 驱动与故障注入步骤，覆盖真实领域命令和自动 maintenance tick。
- [ ] 4. 执行合成规模样本记录瓶颈，仅当数据证明需要才提出独立 worker/增量计算后续。
- [ ] 5. 在临时库执行全项目门禁与迁移；若并行工作失败定位到具体文件，不冒称本任务通过全量。
- [ ] 6. 输出 release-evidence.md 和 runbook.md（实施时），明确 stub 与真实 provider、已验证与尚缺证据。

## 改动边界与重用位置

- `backend/app/services/maintenance.py`
- `backend/app/services/steward.py`
- `backend/app/logctx.py`
- `backend/app/api/admin_deps.py`
- `backend/app/services/admin_audit.py`
- `system-admin-frontend/src`
- `scripts`
- `README.md`
- `docker-compose.yml`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_maintenance.py tests/test_notifications.py tests/test_steward_assist.py tests/test_action_cards_api.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [ ] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [ ] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [ ] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
