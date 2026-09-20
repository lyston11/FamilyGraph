# 实施计划：去除受控 profile 门禁

## 检查清单

### 阶段 A：后端核心

- [ ] A1 `task.py start`（建分支 + worktree）
- [ ] A2 `agent_provider.py`：重写 `provider_profile_error` 为结构性校验；
      删除 9 个 `STANDARD_*` 常量
- [ ] A3 `agent_provider.py`：`_provider_runtime` 里 `resolution.api or STANDARD_API`
      改为缺 api 时返回 `None`（fail-closed，不猜协议）
- [ ] A4 `config.py`：删除 `AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 及注释
- [ ] A5 `admin_agent.py`：两处 422 文案改为中性（不点供应商名）
- [ ] A6 全局 grep 确认 `backend/app` 内无 `liu-dada` / `gpt-5.6-sol` /
      `AGENT_PROVIDER_STANDARD_PROFILE_ONLY` 残留

### 阶段 B：测试同步

- [ ] B1 `tests/conftest.py`：删除开关赋值（3 处）与 `_reset_synthetic_provider_gate`
      fixture
- [ ] B2 `tests/test_agent_admin_providers.py`：改写
      `test_strict_mode_accepts_only_canonical_liu_dada_profile` →
      「任意合规第三方可注册」+「结构非法被拒」
- [ ] B3 `tests/test_agent_provider.py`：改写
      `test_standard_liu_dada_profile_is_enforced_in_strict_mode` 同上
- [ ] B4 新增断言：`cloud_allowed=False` 时第三方云 Provider 仍 `denied_cloud_forbidden`
- [ ] B5 新增断言：`model` 不在 `allowed_models` 内时拒绝（防越权模型名出站）
- [ ] B6 `scripts/steward_e2e.py`：删除对被删常量/开关的引用（保证脚本可运行）
- [ ] B7 `cd backend && ruff check . && ruff format --check . && mypy app && pytest`

### 阶段 C：前端与文案

- [ ] C1 `system-admin-frontend/.../AgentProviderAdminView.vue`：去掉 `liu-dada`
      快捷预设；占位符去 `gpt-5.6-sol`
- [ ] C2 `system-admin-frontend/tests/*.spec.ts`：预设相关断言更新
- [ ] C3 确认 `frontend/` 无硬编码供应商（初查未见，复查）
- [ ] C4 `docker-compose.yml` 注释与 `README.md:146` 改写
- [ ] C5 `cd system-admin-frontend && npm run lint && npm run type-check && npm test`

### 阶段 D：规范同步

- [ ] D1 `.trellis/spec/backend/agent-runtime.md:219` 改写为结构性校验 +
      空间云同意合同（含「`local` ≠ 数据不出网」的明确说明）

### 阶段 E：端到端验收

- [ ] E1 AC1：在**线上管理后台**（经 SSH 隧道）注册一个与 liu-dada 无关的
      第三方 Provider，确认 201
- [ ] E2 AC2/AC3：验证 `cloud_allowed=True` → allowed、`False` → denied_cloud_forbidden
- [ ] E3 AC4：缺 base_url / 非绝对 URL 被拒
- [ ] E4 AC5：全局 grep 零命中
- [ ] E5 AC8：跑定向检查并记录未运行项

### 阶段 F：收尾

- [ ] F1 `verification.md` 逐条 AC 证据
- [ ] F2 archive + 合并 main + push + 清理 worktree
- [ ] F3 报告

## 验证命令

```bash
# A/C：静态清理核对（AC5）
grep -rn "liu-dada\|gpt-5.6-sol\|AGENT_PROVIDER_STANDARD_PROFILE_ONLY" \
  backend/app system-admin-frontend/src frontend/src ; echo "命中数应为 0"

# B：后端门禁
cd backend && ruff check . && ruff format --check . && mypy app && pytest

# C：管理员前端
cd system-admin-frontend && npm run lint && npm run type-check && npm test && npm run build

# E1：线上注册第三方 Provider（经 SSH 隧道，不在公网暴露）
ssh -N -L 8101:127.0.0.1:8101 lyston &   # 管理后台
# 浏览器 http://127.0.0.1:8101 → 注册 base_url=http://100.71.18.78:8787/v1

# E2/E3：解析语义（隔离库上跑，避免污染）
DATA_DIR=/tmp/fg-provider-check .venv/bin/python -c "..."   # 见 verification.md
```

## 风险点与回滚锚

| 节点 | 风险 | 回滚动作 |
|---|---|---|
| A2 结构校验 | 漏掉 `model in allowed_models` 导致越权模型名出站 | B5 专测；出站侧 `_TOKEN_CAP_FIELDS` 仍截断 |
| A3 api 兜底改 fail-closed | 既有行若 api 为空会被拒（行为变化） | 先查线上/开发库 `agent_providers.api` 是否都非空（实测均为 `openai-responses`） |
| B1 删 conftest fixture | 其他测试若依赖开关会失败 | 全量 `pytest` 兜底 |
| C1 删前端预设 | 既有 e2e 断言失效 | C2 同步改 |
| E1 线上注册 | 误把测试 Provider 留在线上 | 验收后在线上删除该行（或只在开发库做 E1，线上仅做只读核对） |

## 部署影响

- **无迁移**，故线上发布不需要迁移核对之外的动作；但 `provider_profile_error`
  是运行期判定，改完必须让线上加载新代码（`bash scripts/deploy-prod.sh`）。
- 发布顺序约束不适用（不涉及新增事件类型）。
