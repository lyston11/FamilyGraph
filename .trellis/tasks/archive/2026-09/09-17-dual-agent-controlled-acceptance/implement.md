# F 实施计划

C/D/E 集成（`main@802925f`）是本任务的执行前提；本任务为验收任务，不改业务代码。
用户已授权主会话内联推进（不使用子智能体）。

- [x] 启动本任务 worktree；固定待验 SHA `802925f`、依赖版本、环境和 C/D/E 验收入口。
- [x] 盘点已有测试覆盖并生成逐格 matrix，注明复用/新增/未覆盖；父任务原五个反例纳入回归映射。
- [x] 准备显式隔离 DATA_DIR、专用端口和 loopback 假上游，`free_ports` 动态分配避开生产 SSH 隧道 8000/8001/8002。
- [x] 运行实际 SDK/真实 backend 的矩阵；隔离库先 `alembic upgrade head`，不用 `create_all`。
- [x] 启动真实 frontend 构建 + 真 sidecar 进程，浏览器验证首帧→提交→等待→工具→正文→终态及刷新；显式测试 proxy，非 route mock。
- [x] 量化 gateway chunk、公共 SSE 接收、渲染及代理/时钟边界；精度与未知明确入报告。
- [x] 执行受影响包门禁与真实 API smoke；失败/blocked 如实保留，不填通过豁免。
- [x] 输出 matrix、前后对照、隐私检查与环境退出证明；记录父 AC01/02/03/04/05/06/08 的本地证据。
- [x] 交 G 部署准入；提交/串行集成/归档与 worktree 清理（不把 G 真实验证算作本任务已完成）。

## 交付物

| 文件 | 内容 |
| --- | --- |
| `scripts/smoke/run_controlled_acceptance.py` | 助手/管家/观测/重试矩阵（29 受控格 + 9 backend 复用格 + 3 sidecar 复用格），自动隔离、退出即清理 |
| `scripts/smoke/controlled_assistant_worker.mjs` | 真实 `SidecarWorker`/`InternalClient`/Pi session 驱动；仅 provider 流被脚本化 |
| `scripts/smoke/run_browser_acceptance.py` | 真浏览器 + 真 sidecar 进程 + 真前端构建（19 格，含取消呈现 UI2-6/UI2-7） |
| `evidence/matrix.md` | 逐格结果、门禁、缺陷、证据边界 |
| `evidence/defect-cancel.note.md` | D-F1 取消语义缺陷 + 判别实验 |
| `evidence/defect-compaction.note.md` | D-F2 压缩归属缺陷 |
| `evidence/browser-claim.png` | 真实浏览器终态截图（合成数据，无真实家庭信息） |

隐私核对：截图与 JSON 证据只含 `DEV_SEED_DEMO_DATA=1` 的合成演示数据
（示例账号「朱元璋」与虚构空间名）、合成问答文本与机器码。无真实用户、真实家庭
信息、token、PIN、prompt 全文或上游响应；临时 DATA_DIR 与浏览器 profile 在退出时删除。

启动/停止方法：两个 Python 脚本自带完整生命周期（迁移 → 启动 listener/sidecar/前端 →
执行 → 终止进程 → 删除临时 DATA_DIR 与浏览器 profile），无需手工清理；
`--report PATH` 写 JSON 证据，`--scenario ID` 可只跑单格。

## 最小验证入口（本任务实际执行）

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m mypy app
cd backend && .venv/bin/python -m pytest -q
cd ../agent && npm run lint && npm run type-check && npx vitest run && npm run build
cd ../frontend && npm run lint && npm run type-check && npm test && npm run build
./scripts/frontend-api-smoke.sh --report /tmp/familygraph-controlled-smoke.json
python3 scripts/smoke/run_controlled_acceptance.py --report /tmp/f-matrix.json
python3 scripts/smoke/run_browser_acceptance.py --report /tmp/f-browser.json
```

报告模板每格均含：场景 ID、源码 SHA、依赖版本、输入 fixture、时序注入、命令、
请求数/状态、阶段指标、容差、结果与证据路径。矩阵明确区分 mock 假上游与真实 Provider
（本矩阵全部为假上游 + 真链路；真实 Provider 属 G）。
