# Implement — Steward 生产调度与运维可靠性

## 开始条件

- [x] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [x] 确认依赖：无；是本轮首个实现任务。
- [x] 取得最新实施摘要的明确批准并完成本任务实现；发布门禁仍为 partial。

## 有序执行

- [x] 1. 先写扫描相同 cursor、失败恢复、旧 lease fence 的失败回归；记录现有 46 条测试基线。
- [x] 2. 补 schedule/job 元数据与迁移，调度器复用 canonical enqueue 合同，禁止直接更新 SourceFact。
- [x] 3. 实现恢复、退避、固定执行水位、租约栅栏和三 listener 生命周期。
- [x] 4. 实现 admin 元数据/重跑命令及最小后台运维入口；审计只保存安全理由分类和关联 ID。
- [x] 5. 更新 Compose/README 中有效开关说明；遵守 scripts/dev-up.sh 启动约定，不改用户 .env。
- [x] 6. 运行本任务测试；将外部模型慢请求不阻塞调度的集成检查交给 assist-execution 联合验收。

## 改动边界与重用位置

- `backend/app/services/maintenance.py`
- `backend/app/services/steward.py`
- `backend/app/models/steward.py`
- `backend/app/config.py`
- `backend/app/main.py`
- `backend/app/serve.py`
- `backend/app/api/admin_deps.py`
- `backend/app/services/admin_audit.py`
- `system-admin-frontend/src`
- `docker-compose.yml`
- `scripts/dev-up.sh`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_maintenance.py tests/test_steward.py tests/test_system_admin_boundary.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [x] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [x] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [x] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
