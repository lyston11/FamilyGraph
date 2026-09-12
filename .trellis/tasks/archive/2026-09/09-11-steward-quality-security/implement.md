# Implement — Steward 模型辅助安全修复与质量评测

## 开始条件

- [x] 阅读 prd.md、design.md、notes.md 与 manifests 指向的 spec/research。
- [x] 确认依赖：`09-11-steward-assist-execution`, `09-11-steward-projection-consistency`。
- [x] 取得最新实施摘要的明确批准并完成本任务实现；发布门禁仍为 partial。

## 有序执行

- [x] 1. 先补缺失 payload policy 和解释恶意输出的失败用例，记录当前缺口而非声称已泄漏。
- [x] 2. 实现受众限定的输入 projector 与 policy adapter，捕获出站确认无 raw/masked/secret。
- [x] 3. 实现闭合输出 schema、证据匹配和模板呈现；与候选审核约定类型映射。
- [x] 4. 补两协议 provider/failure/timeout 和默认全关回归，沿用 assist-execution 预算记录。
- [x] 5. 建立 fixture 评测与 JSON 报告脚本，接入本仓现有检查入口；真实模型评测另存证据。
- [x] 6. 同步 ActionCard 前端只展示被校验的辅助解释，保持确定性隐私影响可见。

## 改动边界与重用位置

- `backend/app/services/steward_assist.py`
- `backend/app/services/steward.py`
- `backend/app/services/policy_guard.py`
- `backend/app/services/agent_provider.py`
- `backend/app/api/action_cards.py`
- `frontend/src/components/actioncard/ActionCardItem.vue`
- `backend/tests/test_steward_assist.py`

## 验证计划

优先运行下列现有测试并按 AC 增加有意义回归；文件名若在实施时调整，以 rg 定位后的真实测试为准。

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_steward_assist.py tests/test_policy_guard.py tests/test_agent_provider.py tests/test_memory_rag_service.py
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m mypy app
```

当前 conftest 已在导入 app 前强制生成临时 DATA_DIR；执行前核对该隔离仍有效，独立迁移/E2E 脚本也必须显式用临时目录。定向通过后在本次变更最终状态跑 backend 全量。涉及前端/后台 UI 的包分别运行 npm run type-check、npm run lint、npm test、npm run build；不涉及 agent/src 的任务不强行改 sidecar。迁移在临时 DATA_DIR 做 upgrade head→down_revision→head，并核验保留数据/索引/约束。

## 回滚与交接

- [x] 按 design 的停用顺序验证，核心授权检查不能随辅助回滚移除。
- [x] 把实测命令、结果、故障/安全限制和剩余项追加 notes.md；没有外部证据不能声称真实 provider E2E。
- [x] 更新所属 spec、父任务 findings/验收清单；不得仅靠归档标记认定修复。
