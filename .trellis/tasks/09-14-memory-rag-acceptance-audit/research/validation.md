# 验证结果与复现入口

## 版本、数据与工具边界

- B/D累计代码：`bd899b98871c02ad97b7791a051f126d671bfefb`，父集成worktree为`/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation`。复查过程中backend/agent/frontend/shared等生产路径对该提交无diff。
- C补充补丁：`8e91c420ce4d8ef2f4a4d806027bd29d72382f94`，只在C专属分支，本地提交并以backup分支固定；未push/合入累计代码或main。
- 所有数据库为真实Alembic初始化的临时库；API使用FastAPI实际路由/服务；Pi固定0.84.3。只有模型流和明确标记的同步点使用可控替身，无真实模型请求、生产库、线上开关或家庭资料。
- 原始报告按原内容保存；日志的逐字节原件保存在probes/raw/*.log.gz，可读.log副本仅清除pytest输出的行尾空格。原件/副本各有hash，gzip解压结果与原输出一致。文件与源码校验见[artifacts.json](artifacts.json)和[code-baseline.json](code-baseline.json)。D探针副本只将固定BACKEND路径改成显式FG_AUDIT_BACKEND，保留原hash和全部断言。

## 已执行结果

| 检查 | 实际结果 | 能证明什么 / 限制 |
|---|---|---|
| A原验收 | backend1050 passed/3既有skip，前端相关56，API56/56 | 沿用原实施证据；本轮未重跑A全包 |
| B原实施汇报 | backend1114、agent109、frontend620，lint/type/build绿 | 保留历史命令事实，不等于下面新反例通过 |
| D原实施汇报 | backend1138、lint/type绿 | 保留历史事实，未重复全套 |
| B独立复查 | 最新18个场景：16失败/2通过，分三批 | 实际鉴权后竞争、引用/重读/预算/补足等；2组静态缺失没有伪造测试结果 |
| D独立复查 | 20个场景：17失败/3通过，分三批 | 双Session、真实tick、stage/降级/固定水位等；明确同步点模拟的边界 |
| B静态检查 | ruff check/format16文件，mypy190文件通过 | 说明类型/风格正常，不消除合同失败 |
| D静态检查 | ruff check/format343文件，mypy190文件通过 | 同上 |
| 冻结检索对比 | 核心3/16→16/16；英文2/2→2/2；扩展0/10→7/10，MRR0.65 | 同一冻结fixture；只证明来源召回，不证明追问消解或模型忠实度 |
| 累计现有API smoke | exit0，56/56 | 真实三listener、Memory来源/确认/撤销链；[报告](smoke/initial-api-smoke.json) |
| 新真实agent-memory smoke | exit0，50/50 | 真实FastAPI、maintenance、InternalClient、SidecarWorker、Pi、事件和读取；两个假模型流、外网尝试0；[报告](smoke/real-agent-memory-smoke.json) |
| C补充修复 | 红测14通过/1失败→相关35通过→完整113通过；独立SDK成功结算硬断言通过 | 代码/测试/spec单独提交8e91c42；未证明真实模型质量/任意长输入成功 |
| 新harness质量 | Python ruff check/format、Node语法检查、实际执行通过；agent build通过 | 未新增业务逻辑，无需为纯记录重跑三端完整包 |

B分批原始日志：[首轮](probes/b-first.log)、[补查](probes/b-additional.log)、[字节正对照](probes/b-byte-control.log)。首轮fallback布置未触发，调整无关消息的创建顺序后有效复现；按同一场景最新结果计失败，不将重跑计成新覆盖。D日志：[首轮](probes/d-first.log)、[补查](probes/d-additional.log)、[真实撤销竞争](probes/d-revocation.log)。

## 真实集成smoke覆盖

1. 启动隔离三listener，部署RAG允许但Agent/Steward关闭；admin平台RAG关闭后真实API创建/确认中文记忆，数据库无document。
2. 平台开启后由实际maintenance循环补齐，真实search命中，无同步全库rebuild替代。
3. 同一临时库配置合成local Provider，启用Assistant runtime。实际sidecar领取job、重复读取context、构建同一Pi manager，并看到本轮来源及一次current user。
4. 第二轮实际包含上一轮user/assistant文字。事件append成功后先通过真实API撤销Memory，再模拟丢失HTTP响应；InternalClient重试得到duplicate。
5. 两个run均succeeded。SSE与固定fallback组成的读取结果、最终history和撤销后search符合正常路径；事件不超16384字节。
6. 每轮只有1次假Provider stream；global fetch只允许本次本地listener，外部网络尝试0。服务、临时数据库和Pi目录随退出清理，报告不含token/prompt/来源正文。

该正常链不包括B-I03的恶意internal保留字段、旧attempt并发、provider admission负例、全部chunk变更/版本竞争或长会话自动压缩；这些分别由独立B/D/C证据承担，未验证部分继续列入F门槛。

### Harness调试与有效证据区别

早期草稿使用了错误演示账户、缺少`/v1`的admin路径和CommonJS解析ESM-only Pi包，分别产生401/404/模块解析失败；均为测试脚手架错误，已修正，未登记为产品缺陷。

随后草稿错误要求每条SSE原生带引用/不可用数量，50项中2项不通过。批准的设计允许固定fallback补齐，故改为验证SSE+fallback的有效结果；[该草稿](smoke/harness-strict-sse-draft.json)标记为harness_draft_not_product_failure。B-I03的自报假引用实证来自另一独立探针，仍然失败，没有因该修正被消除。最终任务内的可移植harness副本已再次实际运行，50/50通过。

## 重现B/D反例

下面仅针对隔离候选checkout；不要在生产环境执行。路径变量用于指定当前任务材料和待测代码，不切换现有分支。当前失败是记录中的预期红色结果，不应改断言来制造绿灯。

```bash
FG_AUDIT_DIR=/Users/lyston/PycharmProjects/familygraph/.trellis/tasks/09-14-memory-rag-acceptance-audit
FG_AUDIT_CHECKOUT=/private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation
cd "$FG_AUDIT_CHECKOUT/backend"
PYTHONPATH=. .venv/bin/python -c 'import app; print(app.__file__)'

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -P -m pytest \
  -p conftest -p no:cacheprovider -q -s --tb=short \
  "$FG_AUDIT_DIR/research/probes/test_b_contract_probes.py"

FG_AUDIT_BACKEND="$FG_AUDIT_CHECKOUT/backend" \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -P -m pytest \
  -p conftest -p no:cacheprovider -q -s --tb=short \
  "$FG_AUDIT_DIR/research/probes/test_d_contract_probes.py"
```

`-P`与`PYTHONPATH=tests:.`确保外部probe使用`backend/tests/conftest.py`，避开同名`backend/conftest.py` shim；共享editable venv必须验证app.__file__。D副本的显式BACKEND验证保留，不能让错误checkout静默通过。

这些脚本是原诊断证据，转为长期回归时要保留事实触发和目标合同，同时去除只适用于坏实现的观察前提。例如B预算probe曾要求实际返回至少6块再证明超预算；修复后的回归应要求“输入有6个合法候选”，再断言纳入包装受预算，不能继续强求6块都纳入。合成v3换版测试也须配合明确的受支持算法fixture。任何适配记录diff，不改冻结检索材料/期望来源来追求分数。

## 重现冻结检索与真实正常链

仍使用上面的两个路径变量。检索数据SHA256为`92f0bed8c438a0bfde6672d71e6bb47db0f039c4cd1ac07fc490a7b3ac6c675a`，核心/扩展材料未改变。`test_retrieval_probe_a.py`保留A时脚本；累计版本脚本只增加`attempt=run.attempt`以适配执行合同。

```bash
cd "$FG_AUDIT_CHECKOUT/backend"
FG_RAG_PROBE_REPORT=/tmp/familygraph-audit-retrieval.json \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -P -m pytest \
  -p conftest -p no:cacheprovider -q -s --tb=short \
  "$FG_AUDIT_DIR/research/retrieval/test_retrieval_probe.py"

cd "$FG_AUDIT_CHECKOUT/agent"
npm run build
cd "$FG_AUDIT_CHECKOUT"
PYTHONPATH=scripts/smoke backend/.venv/bin/python \
  "$FG_AUDIT_DIR/research/smoke/run_agent_memory_smoke.py" \
  --report /tmp/familygraph-audit-agent-memory-smoke.json
```

probe固定fixture中的`core_protocol`为原冻结时路径，保留原字节以核验哈希；对应材料现在仍见父任务research/retrieval-cases.md。无资料NE01/NE02可能词面召回附近材料，此处未执行回答模型，不能据此宣布幻觉，也不能宣称无依据追问已验收。

## 未执行的检查与原因

本轮未重跑B/D已有1138级别全套或前端完整套件：业务代码未改，独立反例已足够判定未通过；原检查与新静态检查分别保留。没有真实浏览器可视交互、真实Provider/p95/成本实验、生产库迁移、线上开关核对、main合并或部署。新修复后应按F-01～12和相关包范围重新验证，不能复用本轮红测或正常链结果替代修复验收。
