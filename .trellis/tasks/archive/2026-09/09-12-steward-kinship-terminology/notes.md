# Notes — Steward 个人亲属称谓计算与家族树投影闭环

## 2026-09-12 执行记录

### 根因定位

- 隔离 fixture 验证：`viewer -> son -> spouse` 的 resolver 输出为 `Dm-Sf`，`TermRegistry` 命中 locale `zh-CN` 的“儿媳”，PFV edge 持久化 `term=儿媳`、`term_source_level=locale`。
- 本地 `backend/data/db/app.db` 检查到旧 PFV 行仍为 `computation_version=pfv-v1`、`policy_version=graph`，其历史 edge 保存的是结构描述（例如“你的妻子”“你的儿子”）。词典表本身已包含 `Dm-Sf -> 儿媳`。
- 当前服务端 `view_is_current` 已拒绝旧 v1/graph 视图，`_view_payload_for_view` 返回安全空内容并由 API 登记重算；因此旧投影不能继续作为有效快照。截图中的旧文案来自旧投影/旧运行进程或尚未完成自动 tick，不是前端自行把“儿媳”翻译成结构文案。

### 本轮实现

- 新增 `test_pfv_persists_daughter_in_law_term_for_dm_sf`：固定截图黄金用例，断言 resolver concept code、PFV edge term 和来源级别。
- 新增 `test_legacy_pfv_version_cannot_serve_structural_snapshot`：旧 v1/graph 投影不得继续提供旧结构文案，只返回安全空内容并给出 `version_drift`。
- 未修改 SourceFact、TermRegistry、前端关系推断逻辑；现有 frontend 已只消费后端 `edge.term`。

### 验证

- `backend/.venv/bin/python -m pytest -q tests/test_personal_family_view_consistency.py tests/test_personal_family_view.py tests/test_terms.py tests/test_relationship_resolver.py`：81 passed。
- `backend/.venv/bin/python -m pytest -q`：969 passed, 3 skipped。
- `backend/scripts/steward_e2e.py`：退出码 0，真实 API + maintenance 自动 tick、PFV 重建及模型辅助隔离通过；该脚本当前使用 fake provider/stub，不是真实外部 Provider。
- frontend 家族树/关系面板定向测试：22 passed；frontend 全量测试与 build 命令通过；type-check、lint 通过。
- `task.py validate`：通过。曾因依赖任务已归档导致 manifest 路径失效，已更新为 `.trellis/tasks/archive/2026-09/...`。

### 剩余发布证据

- 真实 Provider 仍按 release-observability 口径单独记录，未以 fake transport 冒充；未获得真实 Provider 证据时保持 partial。
- 本地旧库需要让当前 backend/worker 运行一次自动 tick，或通过现有重新加载动作触发 PFV 重算；不要手工复制/改写生产 SQLite。
