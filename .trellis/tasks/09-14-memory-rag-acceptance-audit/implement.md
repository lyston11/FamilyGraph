# 实施计划：复查交付与后续串行修复

## 本轮执行边界

用户本次授权创建任务并记录分析；状态保持 planning。下面“后续修复”是可审阅计划，本轮不执行 B/D 业务变更，不合并 main、不 push/deploy。先前授权的 C 补充修复已独立保存为 `8e91c42`，不是本计划勾选完成的修复波次。

## 本轮分析交付

- [x] 用 task.py create --parent 创建本任务，保留父任务 in_progress。
- [x] 锁定累计基线 bd899b9，记录 main/各任务分支，保留无关工作区改动。
- [x] 消费 B/D 两份独立只读报告，主线程沿原源码抽查决定性结论。
- [x] 保存 20 组问题、原 MR-01～26 映射、12 项能力建议及唯一所有者。
- [x] 保存合成探针与原始分批输出、冻结检索集和结果，区分静态与未测。
- [x] 完成 PRD、design、implement、验收矩阵和两个非空 context manifests。
- [x] 在隔离监听器与实际 sidecar/Pi 上完成正常两轮/撤销/丢响应重试 smoke，记录其验证范围。
- [x] 更新父任务当前验收结论，保留历史测试与提交记录。
- [x] 校验 Trellis 清单、本地文档链接、证据哈希和生产代码未变；结果见 research/planning-validation.md。

## 后续修复波次（尚未执行）

进入业务修复时从主检出读取实际 task 状态/最新分支，按既有工作流激活对应 B/D 任务，在其专属 worktree 提交。先整合审阅后的本方案与原 PRD；本任务不复制一个竞争实现所有者。

| 波次 | 责任与内容 | 对应发现 | 放行条件 |
|---|---|---|---|
| 0 | 父集成责任：冻结当前反例与数据、核对新 head/依赖，安排 C 8e91c42 在累计分支的纳入点 | C-I01、MR-24 | 不改变原审计基线；明确哪些补丁已进入候选 |
| 1 | B：签名 attempt 到实际 writer/admission、context 并发/失效、内部 reference 与指纹 | B-I01/I05/I10 | 五个鉴权后换代反例通过；新增 provider admission 反例；同 attempt 并发/关闭无 500/旧材料 |
| 2 | B：精确片段证据、统一引用认证/投影、固定补取定位、前后端协议 | B-I02/I03/I04/I09 | 删除/改文/改版本拒绝，SSE/internal 假或撤权引用不流出，跨会话 key 不串行 |
| 3 | B：真实 RAG 子预算、授权后有界补足、唯一追问 | B-I06/I07/I08 | 正反追问可区分词面命中；实际包装受预算；冻结核心/英文不退化 |
| 4 | D：document 唯一性、镜像预检、chunk/staging 不可变，先封住危险 downgrade | D-I01/I02/I08 | 两 Session 唯一、冲突不覆写；脏迁移/旧依赖降级无损拒绝 |
| 5 | D：持久条件租约、完整回滚、最终开关/来源检查、目标版本语义 | D-I03/I04/I05/I06 | 旧游标/策略不回写；真实 tick 无部分提交；换版检索可用、下一批不降级 |
| 6 | D：固定水位全轮、投影完整性、合法 FTS repair | D-I07/I09/I10 | 持续新增时低 ID 仍恢复；缺块/缺 FTS 恢复，非法来源不算合法修复 |
| 7 | 父集成责任：累计版本、实际 listener+SDK 负例、包检查与验收回填 | F-01～12、父 AC-03～10 | 每个门槛有真实结果；没有未解释的 500、失租写入或破坏降级 |

波次 1～6 全部串行，不能因 B/D 标签不同并行改 memory_rag、schema 或迁移。波次 2 的精确证据可先检测漂移；D 波次 4 完成后再用同一回归确认写端不可变，不另造 resolver。

## 文件责任

| 所有者 | 主要文件/模块 |
|---|---|
| B backend | services/agent_tokens.py、agent_queue.py、agent_events.py、agent_tools.py、provider_proxy.py、context_builder.py、memory_rag.py、rag_query.py；api/internal_agent.py、api/agent.py；schemas/agent.py、models/context.py 及相关迁移/测试 |
| B sidecar/frontend | agent 的 client/events/worker/types；frontend 的 agent store、引用 parser、现有 CitationList/MessageList；保留 C session/worker 回归 |
| D backend | services/memory_rag.py、rag_maintenance.py、maintenance.py、memory_sources.py 的必要复用；models/rag.py、索引迁移及真实多 Session 测试 |
| 父集成责任 | scripts/smoke 的真实 API+sidecar 链、累计迁移验证、AC 回填、最终合并/归档/清理（需相应授权） |
| E / 既有后续任务 | 全请求预算/跨 Run 摘要及能力评估；MR-23/26、Steward shared RAG/开关/称谓由 coverage-and-roadmap.md 指定所有者承担 |

## 复现及检查入口

先确认代码来自目标 checkout，避免共享 editable venv 加载 main：

```bash
cd /private/tmp/familygraph-memory-rag/09-13-agent-memory-rag-remediation/backend
PYTHONPATH=. .venv/bin/python -c 'import app; print(app.__file__)'
```

本任务 probe 副本的命令与原执行记录见 [验证记录](research/validation.md)。后续实现先把反例转为适合仓库的回归，保留真实同步点/独立 Session；不能把唯一键约束、人工 attempt+5 或同 Session 两次调用当成完整并发证据。

每个波次先跑影响范围，最终累计候选按 AGENTS 执行：

```bash
# 各命令在相应包目录中运行，避免导入另一 checkout。
ruff check .
ruff format --check .
PYTHONPATH=. mypy app
PYTHONPATH=.:tests pytest
npm run lint
npm run type-check
npm test
npm run build
```

npm 检查分别覆盖实际受影响的 agent/frontend；只有涉及管理员 UI 才纳入 system-admin-frontend。跨端字段变更不能只以单端 mock 通过放行。migration 先查实际 heads，再用全新 DATA_DIR 执行 upgrade；分别加入合法、失效、重复、镜像冲突、历史 NULL、旧版本保存依赖样本。无损 downgrade 不满足时应拒绝，不以删除数据使往返绿灯。

最终最小真实联调包括：关闭保存→RAG-only 晚开启补齐→run lease/context→Pi 历史恢复与引用→真实事件写入→丢响应撤权重试→SSE/历史/固定补取；另外加入新的恶意内部保留字段、鉴权后换代、精确片段和版本竞争反例。当前正常链 smoke 的通过不代替这些负例。

## 回滚点与完成条件

- B 协议升级先后端兼容读取/写入，再 sidecar/frontend；旧缺 attempt token 继续拒绝，不从数据库补签名身份。
- 索引约束前的元数据预检失败时停在原可审计数据状态。暂时关闭受影响新写入/维护职责优先于破坏性清理；线上开关操作不在本轮授权内。
- 指针切换前发生冲突，保留原合法活动版本；换版后回退也必须重验来源和执行策略，不能让旧 worker 自行降级。
- 所有修复结果更新本任务 ledger 和原 B/D implementation 记录；原测量与失败证据保持历史可追踪，F-01～12 按实际结果重新标记。
- main 合并前可以在隔离累计分支完成 smoke；“未授权合并 main”不等于“不能做隔离集成验证”。真正环境阻塞用退出码 2，不计通过。
- 后续由单一串行集成通道合并、archive 后，且 worktree 无未提交业务改动，才删除 worktree 和已合并分支。本轮未满足这些条件，保留现场。
