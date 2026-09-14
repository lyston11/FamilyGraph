# B 真实传输烟测补充

日期：2026-09-14。范围是 `scripts/smoke/run_agent_memory_smoke.py`、`agent_memory_worker.mjs` 和新增的 `agent_memory_negative_worker.mjs`；没有修改生产模块、迁移、其他测试或冻结审计证据。

## 验收行为

保留原 50 项正常链路检查：隔离迁移、真实三个 listener、RAG 关闭时保存与确认、RAG-only 晚开启补齐、两轮真实 InternalClient/SidecarWorker/buildRunSession/Pi、当前 user 只加入一次、同一 manager 恢复历史、提交成功后撤权并丢失响应、同请求 duplicate、公开历史和 SSE 加固定补取、公开 payload 字节上限。

本次加入一个真实保存、可召回而最终回答未引用的合成来源。两轮回答仍只使用主来源；scripted partial 提及另一来源，completed snapshot 只引用主来源。撤销后按主来源 ID 检查不可检索，同时断言第二来源仍可检索，避免沿用单来源 fixture 的“整个结果必须为空”条件。

| 路径 | 新增检查 |
|---|---|
| 实际 lease / context HTTP 响应 | 记录纯运行绑定，在内存中核对 leased attempt 与 context attempt |
| 实际 assistant append HTTP 请求 | `context_reference={build_id,attempt,used_handles}` 匹配本次 context 和 lease；只有 completed text 使用的主句柄，不包含另一来源；非 assistant 事件无 reference；公开 payload 无私有引用字段 |
| 丢响应后的再次发送 | 对照原始 assistant 请求整体，确认 reference 和正文保持原值；继续要求服务端返回 duplicate |
| 两轮公开读 | SSE、Last-Event-ID 重连和固定补取一致；私有 provenance 不外露；第二来源不被列为已引用或不可用来源 |
| 撤权后的第三次真实 lease | 直接观察客户端解析前的 raw internal context；历史无原结构化引用/provenance，前两轮正文逐消息保持；新 context 排除主来源、包含仍合法的第二来源 |
| 有效 run token 的自报字段 | 提交假六字段 citation、摘录、不可用数量、私有定位/public context_reference；允许明确 4xx 或安全剥离；拒绝后用相同 seq 的普通正文证明原请求未占位且 token 仍有效 |
| 无绑定与错误绑定 | 对当前 included、仍可读的第二来源提交无 reference、错误 build、错误 attempt、正文未使用却自报 used_handle 四种反例；任何反例均不产生认证引用 |
| 反例后的所有读取 | raw internal history、正常 SSE、重连、浏览器 history 与每条消息的固定补取无假来源、假摘录或自报数量；普通正文保留 |

第三次执行仅用真实 InternalClient 提交协议反例并 settle，不调用模型。前两轮继续使用真实 Pi SDK，只替换模型流；所有请求限于动态分配的本地 listener。报告仅输出检查值、计数、HTTP 状态与错误类型，不输出 token、资料或提示正文。

## 当前验证状态

- Python AST 与两个 Node `--check`：通过。
- Python `ruff check` 与 `ruff format --check`：通过。
- 主线程在 B 生产代码冻结后执行完整烟测：**95/95 通过，退出码 0**。报告见 [acceptance-smoke.json](acceptance-smoke.json)，SHA-256 为 `f3420b7bc17c8adf4275b146a9b5cc63244a5c23b9f10ce9091132a4df7f4171`；两个 Pi 执行及第三次协议反例 lease、原请求重放、全部读取投影和网络限制均通过。运行基线为 `7f2e88c` 加本轮待提交 B 修复；若独立复查继续修改相关行为，重跑受影响验证。
- 主线程在末轮 context 回滚/空构建及 LIKE 修复后再次执行：**95/95，退出码 0**，报告与上述文件字节相同。原审计目录的脚本副本和历史报告保持不变。最终累计分支仍须在 D 修复后重新运行。

生产代码稳定后的执行命令：

```bash
cd /Users/lyston/PycharmProjects/fg-09-13-rag-retrieval-citations/agent
npm run build
cd /Users/lyston/PycharmProjects/fg-09-13-rag-retrieval-citations
PYTHONPATH=scripts/smoke backend/.venv/bin/python \
  scripts/smoke/run_agent_memory_smoke.py \
  --report /tmp/familygraph-b-memory-contract-smoke.json
```

独立 DATA_DIR、Alembic 和 listener 由 Python 脚本创建并清理；不连接生产数据库。退出码 2 表示环境阻塞，不算通过。该烟测证明协议接线、受权投影和正常执行路径，不证明真实模型的语义忠实度、线上延迟或全请求 token 预算；精确 16384/+1 边界仍由 B 的专项回归单列验收。
