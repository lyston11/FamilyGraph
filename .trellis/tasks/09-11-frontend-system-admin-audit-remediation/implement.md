# Implement — 前端与系统后台设计、接口连通性与发布质量整改

## 开始条件

- [ ] 读取 `prd.md`、`design.md`、`implement.jsonl` 和相关 spec；确认当前任务仍为 planning。
- [ ] 保留工作区已有未相关修改，不执行 reset/clean，不把其他任务文件纳入提交。
- [ ] 核对依赖任务：Steward production-ops/release-observability 只提供可消费的安全观测合同；本任务不重复实现其后端调度。
- [ ] 用户明确批准本规划摘要后，才运行 `python3 ./.trellis/scripts/task.py start frontend-system-admin-audit-remediation`。

## 有序实施清单

### Phase A — 媒体和错误合同

- [ ] 新增家庭端带 Bearer 的附件 blob/预览 helper，复用现有 token reader、refresh single-flight 和 `ApiError`。
- [ ] 为 AttachmentsSection 加载、重试、object URL 释放、无权安全态和键盘/屏幕阅读器反馈。
- [ ] 清理 household/stats/notifications 的过时占位注释和错误文案，保留真实 404/403/503/disabled 分类。
- [ ] 补充 multipart、ETag/304、附件 401 refresh 和 URL 不含 token 的单元/契约测试。

### Phase B — 真实 API smoke

- [ ] 建立临时 DATA_DIR、合成 seed、三 listener 和前端代理的可重复启动/teardown 脚本；默认不触碰用户 `.env` 或业务库。
- [ ] 实现家庭认证、空间、家庭卡/PFV、统计、通知、记忆、Agent SSE、附件和后台认证/概览/敏感详情/治理的 smoke 用例。
- [ ] 增加家庭 token↔admin listener、admin token↔家庭 listener 的交叉拒绝和 8000 `/admin-api/*` 普通 404 断言。
- [ ] 输出脱敏 JSON 证据；环境不可用标记 blocked，真实 provider 不可用标记 partial/degraded。

### Phase C — 共享视觉基础和后台响应式

- [ ] 定义共享品牌 token/别名和状态组件基线；保持 `--fg-*`、`--ag-*` 向后兼容。
- [ ] 改造 AdminShell 移动导航、账户菜单、focus/ARIA、body overflow 和表格/卡片响应式。
- [ ] 在 375px、768px、桌面视口增加组件测试或浏览器截图断言；确认家庭端现有主题与壳布局不回归。

### Phase D — 运维观测面板

- [ ] 盘点现有 `/admin-api/v1` DTO 和 Steward ops 子任务合同，先复用已有 endpoint，不在前端猜字段。
- [ ] 添加安全指标卡、状态解释、刷新/失败/空态和可见性暂停轮询；敏感详情仍使用 access-session。
- [ ] 若重跑接口已可用，接 reason/idempotency/audit UI；否则显示明确 disabled 状态并记录依赖，不新增旁路写操作。
- [ ] 增加响应负向断言：无 prompt、姓名、家庭内容、token、provider 原始错误。

### Phase E — 性能和测试治理

- [ ] 修正动态 import 与静态 import 冲突；将 Three.js、Agent 和重型管理页按需加载。
- [ ] 记录构建 chunk 基线和阈值；只有产物证据支持时才调整 `chunkSizeWarningLimit`。
- [ ] 修复 jsdom/XHR/router 警告的测试 setup 或组件 mock，不通过静默 console 来掩盖失败。
- [ ] 更新 README/runbook，说明启动全栈、smoke 前置条件、blocked/partial 解释和脱敏证据格式。

## 验证命令

```bash
# 家庭前端
cd frontend
npm run type-check
npm run lint
npm test -- --run
npm run build

# 系统后台前端
cd ../system-admin-frontend
npm run type-check
npm run lint
npm test -- --run
npm run build

# 后端现有门禁
cd ../backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app

# 真实 smoke（隔离 DATA_DIR；按项目启动约定）
cd ..
./scripts/dev-up.sh
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-smoke.json
```

若本机不能运行完整栈，必须保存 blocked 原因和已执行的 listener/TestClient 替代证据；不得把单元 mock 结果写成真实接口通过。

## 风险文件和回滚点

- `frontend/src/api/attachments.ts`、`frontend/src/components/member/AttachmentsSection.vue`：媒体认证回滚点。
- `frontend/src/api/{household,spaceStats,notifications}.ts` 与对应 views：错误语义回滚点。
- `frontend/src/styles/*`、`system-admin-frontend/src/styles/main.css`、`system-admin-frontend/src/components/AdminShell.vue`：视觉/响应式回滚点。
- `system-admin-frontend/src/views/AgentMonitorView.vue`、`OverviewView.vue`：观测回滚点。
- `frontend/vite.config.ts`、动态 import 入口和 `vitest.setup.ts`：性能/测试回滚点。
- smoke scripts/docker/nginx：环境验证回滚点；不得修改生产数据目录。

## 交付和完成门禁

- [ ] 每个 R1–R7 至少有一个可复现测试或明确的 blocked/deferred 证据。
- [ ] 运行 `python3 ./.trellis/scripts/get_context.py --mode packages`，对 frontend/backend 两层执行 Quality Check。
- [ ] 运行 trellis-check 全范围复核：契约、隔离、可访问性、性能、脱敏、跨层数据流。
- [ ] 判断是否需要更新 `.trellis/spec/`；若产生新的 token、smoke 或媒体访问约定，先更新 spec 再提交。
- [ ] 仅提交本任务代码和文档；按 Trellis 要求在提交前列出未识别 dirty 文件并请求一次性确认。
- [ ] 生成 `release-evidence.md`（实施时）记录命令、时间、环境、结果、blocked/partial 和未验证 provider；不得宣称未执行的真实 E2E。
