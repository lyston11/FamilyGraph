# A 真实 listener 验收补充

当前 scripts/smoke/run_api_smoke.py 只 GET /memories，无法捕获已复现的创建 422/提交后 500。本次主线程仅扩展这个现有隔离 smoke：显式来源创建、候选列表、确认/重试、RAG 保存与来源撤销。它启动临时 DATA_DIR 和随机端口，不接开发/线上数据，不调用模型。

主线程拥有该脚本；后端和前端实现代理保持各自业务文件范围。验收使用 research/api-contract.md 的实际字段，正文、来源定位、幂等键与凭据不写报告；HTTP 路径继续模板化。脚本语法/静态检查先做，完整三 listener smoke 等 A 后端合约稳定后执行。

## 执行结果（2026-09-13）

- `./scripts/frontend-api-smoke.sh --report /private/tmp/familygraph-memory-rag-smoke-a.json`：最终退出 0，56 项检查全部通过，其中 Memory 新增链 26 项。脱敏报告见 [real-api-smoke.json](real-api-smoke.json)。
- 首次退出 2：现有 smoke 只随机 public/admin 端口，internal 仍默认 8001；独立 bind 探针证实该端口已被占用（errno 48）。未停止或修改占用者。脚本改为同时分配三个互不重复的临时端口后通过，首个阻塞不计为通过。
- 所有 app 子进程显式 `PYTHONPATH=当前 worktree/backend`，避免链接依赖环境的 editable install 指向主检出。迁移和 listener 均使用本 worktree 源码。
- 脚本 `ruff check` 通过。使用独立临时迁移库，结束后服务/临时数据/凭据清理；未调用真实模型。
