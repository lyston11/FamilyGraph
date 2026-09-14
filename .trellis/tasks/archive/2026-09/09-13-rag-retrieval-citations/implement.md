# Implement：中文 RAG 与引用闭环

## 前提

任务 in_progress，2026-09-14 继续修复验收。累计候选已包含 A、初版 B/D、C 补丁 8e91c42 与 main d7629df。阅读 [复查设计](../09-14-memory-rag-acceptance-audit/design.md)、[B 反例](../09-14-memory-rag-acceptance-audit/research/b-integration-check.md) 和 [父设计](../09-13-agent-memory-rag-remediation/design.md)。D 等待 B 的执行身份、精确片段与检索合同稳定。

## 本轮修复验收

- [x] B-I01/I05/I10：不可变 signed attempt 进入 writer/admission；context 并发唯一与持久失效；内部 context_reference 及原请求指纹端到端。
- [x] B-I02/I03/I04/I09：精确片段认证，统一所有读取投影，保留字段服务端独占；固定补取精确定位。
- [x] B-I06/I07/I08：真实包装预算、有界授权补足、有限同会话唯一锚点与歧义降级。
- [x] 主线程代码核验；冻结核心/英文不退化；C、原请求幂等和精确 16 KiB 正对照保留。check 子代理限流不计为通过，具体核验和末轮三项补修见 [acceptance-check.md](research/acceptance-check.md)。

## 初版执行记录（以下历史勾选不能代替本轮复验）

- [x] 固定核心中文、英文回归、扩展集和权限反例；记录旧实现 Recall@5/排名与扫描量，禁止读真实家庭正文凑样本。
- [x] 实现内部 QueryPlan、规范化/有限词项/受控别名和明确追问解析；上下文不足有明确降级。
- [x] 为所有召回分支复用 A resolver，补短词和二次过滤后的有界补足，测试异常/特殊字符。
- [x] 引入确定性句段切块、有限重叠、index_version 与定位稳定合同；与 D 明确有效版本切换。
- [x] 修 RAG 子预算/排除理由，单列仍未解决的全请求预算，不随便 recent-N 截历史。
- [x] 定义 attempt/context_build_id/included item/citation_handle 合同与同 attempt context 幂等；补 signed attempt、当前执行核验及旧 token 兼容方案，再同步 client/worker/events。
- [x] 以原始请求指纹实现认证后事件的幂等，不拿认证结果比较候选输入；测丢响应、撤权后重试和异参冲突。
- [x] 在 api/agent.py 的历史与 SSE 读取面复用当前授权投影，把有效引用接到既有 UI；同步 unavailable_citation_count parser/显示，覆盖未使用/编造/错误绑定/撤销/失权/重放。
- [x] 实现按 run_id/seq 授权补取完整引用的固定后备，统一消息存储/历史/SSE 投影；测 16 KiB UTF-8 总 payload、web 引用并存、正文占满和补取失败，不截断正文。
- [x] 执行受影响包检查；将 index_version、稳定 chunk、兼容策略及测试结果交接 D。

## 验证命令

backend：`.venv/bin/ruff check .`、`.venv/bin/ruff format --check .`、`.venv/bin/mypy app`；选择 memory_rag/context/policy、internal_agent_api、agent_events、schema_contract 和新中文数据集用例，依最终改动扩展 pytest。
agent：`npm run lint`、`npm run type-check`、`npm test`、`npm run build`，含 C 的压缩回归和真实合同假 Provider 联调。
frontend：`npm run lint`、`npm run type-check`、agent store/CitationList/MessageList 的相关测试、`npm run build`。
集成时运行项目 frontend-api-smoke；环境阻塞不视为验收通过。本轮 B 后端最终 1201 passed / 3 skipped，agent 118、frontend 625；真实 listener/Pi smoke 末轮 95/95。详见 [核验记录](research/acceptance-check.md)，D 完成后的最终累计 smoke 仍待执行。

## 完成与回滚

B-AC1～8 每项有证据，核心集与扩展集分别报告，权限反例不得为提高召回而放宽。回滚切分/查询策略时保留来源与版本，不把旧句柄重绑定到新文本；引用字段的兼容读取先保留。真实模型回答忠实度和线上延迟没有测到就明示未测，不能用合成流通过代替。
