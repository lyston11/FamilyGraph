# Implement — Steward 个人视图、称谓与失效传播修复

## 开始条件

- [ ] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [ ] 确认依赖：`09-11-steward-production-ops`。
- [ ] 取得最新实施摘要的明确批准再 task.py start；当前仅规划。

## 有序执行

- [ ] 1. 逐个采样 actual event producer，建立 fixtures 与事件影响矩阵，先复现前缀/全局失效缺失。
- [ ] 2. 实现统一影响解析并同时用于 invalidation、queue、建议失效。
- [ ] 3. 补合法访问后 PFV ensure/init，保持 account.claimed 两入口，处理已存在 self-registration。
- [ ] 4. 替换 purpose 充当 policy version，接入词典解析和版本；修复 per-view savepoint。
- [ ] 5. 实现完整路径与替代路径重验、授权优先 ETag、无隐式未提交缓存的 GET。
- [ ] 6. 跑 backend PFV/bridge/terms/claim/recommendations 与 frontend 页面/store 缓存回归。

## 改动边界与重用位置

- `backend/app/services/domain_events.py`
- `backend/app/services/personal_family_view.py`
- `backend/app/services/steward.py`
- `backend/app/services/terms.py`
- `backend/app/api/personal_family_view.py`
- `backend/app/commands/registration.py`
- `backend/app/commands/spaces.py`
- `frontend/src/api/personalFamilyView.ts`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_personal_family_view.py tests/test_personal_family_bridge.py tests/test_family_recommendations.py tests/test_terms.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [ ] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [ ] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [ ] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
