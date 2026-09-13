# Implement：中文 RAG 与引用闭环

## 前提

保持 planning。A 的来源授权和 C 的 Pi 回归交付后，在主检出 start B、检查分支/worktree 并进入该 worktree。阅读 [父设计](../09-13-agent-memory-rag-remediation/design.md) 与 [数据集和验证协议](../09-13-agent-memory-rag-remediation/research/validation-plan.md)。后续 D 等待 B 的分块/版本合同。

## 执行顺序

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
集成时运行项目 frontend-api-smoke；环境阻塞不视为验收通过。此处为计划命令，本轮没有重新执行业务测试。

## 完成与回滚

B-AC1～8 每项有证据，核心集与扩展集分别报告，权限反例不得为提高召回而放宽。回滚切分/查询策略时保留来源与版本，不把旧句柄重绑定到新文本；引用字段的兼容读取先保留。真实模型回答忠实度和线上延迟没有测到就明示未测，不能用合成流通过代替。
