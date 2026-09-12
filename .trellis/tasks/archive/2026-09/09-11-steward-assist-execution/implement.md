# Implement — Steward 模型调用事务隔离、预算与崩溃恢复

## 开始条件

- [ ] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [ ] 确认依赖：`09-11-steward-production-ops`。
- [ ] 取得最新实施摘要的明确批准再 task.py start；当前仅规划。

## 有序执行

- [ ] 1. 用独立连接构建慢 transport 写锁复现，先保存观察结果。
- [ ] 2. 设计 batch/attempt migration 并加入 conftest 子表清理顺序；新增 core 和 assist 分阶段测试。
- [ ] 3. 把 _execute_locked 内网络调用移到提交后的辅助执行器；创建 batch 与 core 在同一短事务。
- [ ] 4. 实现预算预留、usage 校验、byte/deadline 上限、unknown 处置和有限执行并发。
- [ ] 5. 加入回写版本/权限/设置 fence；与 quality-security 共享发包政策适配器，不复制授权逻辑。
- [ ] 6. 完成故障注入和 migration 往返；更新“savepoint 防回滚”旧说明与真实 crash 合同。

## 改动边界与重用位置

- `backend/app/services/steward.py`
- `backend/app/services/steward_assist.py`
- `backend/app/services/maintenance.py`
- `backend/app/models/steward.py`
- `backend/app/config.py`
- `backend/tests/conftest.py`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_maintenance.py tests/test_steward.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [ ] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [ ] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [ ] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
