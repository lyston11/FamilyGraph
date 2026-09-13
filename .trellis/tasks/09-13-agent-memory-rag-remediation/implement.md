# 实现计划：双 Agent RAG 与记忆治理

## 当前阶段

规划已经完成，用户随后明确回复“执行”。按 A→C→B→D 推进修复，E 执行评估职责；部署和默认开启可选能力仍不在范围内。A 已启动独立分支/worktree，父任务负责集成验收，不在主检出同时改业务模块。

当前主检出基线已前进到 20d0308，实施者需重读实际代码；原审计证据仍标记 b7bd368。为符合当前沙箱写入范围，任务 worktree 建在 /private/tmp/familygraph-memory-rag/ 下；after_start 已识别并记录实际路径，分支和隔离规则不变。已有主检出/其他 worktree 的用户改动保持独立。

## 规划交付清单

- [x] 一个父任务、五个 child task，通过 task.py create --parent 建立关系。
- [x] 26 项发现按复现/静态/边界/条件风险分类，保留命令、数值和源码锚点。
- [x] 完成全部 PRD/design/implement、context manifests、覆盖矩阵与验证计划；独立只读交叉审阅建议已收敛，材料供用户整体审阅。
- [x] task.py validate 六个任务通过；Markdown 本地链接、ID 覆盖、父子关系、planning 状态和实际注入上下文完整性已检查。
- [x] 审阅入口和延期事项已写入；仍保持 planning，业务修复和能力实验尚未执行。

## 执行顺序

| 波次 | 子任务 | 前提与交接 |
|---|---|---|
| 1 | A memory-contract-repair | 固定来源 DTO/模型/权限依赖、幂等与迁移；真实 API 链先红后绿 |
| 2 | C assistant-context-compaction | 不依赖 A 数据迁移；默认串行避免工作区混乱；固定 Pi 恢复/压缩回归 |
| 3 | B rag-retrieval-citations | 复用 A 的来源授权；沿 C 修复后的 session 行为接 context 与引用；先检索后引用 |
| 4 | D rag-index-lifecycle | A/B 的来源、chunk/index_version 稳定；先防复活和幂等，再接维护触发 |
| 评估 | E agent-memory-capability-plan | 只读可并行；MR-23/MR-26 先复现/设计，不自动修改 Steward 代码 |
| 集成 | 父任务 | 全链路、权限矩阵、迁移与 smoke；逐项回填验收，不把研究结论当实现 |

技术顺序比问题优先级更细：中文召回仍是 P1，但 Pi 与引用会修改相交 sidecar 文件，串行能减少合同漂移。需要调整顺序时先更新本表及双方文件所有权。

## 每个实施子任务的启动步骤

1. 在主检出执行 current/validate，确认当前任务和依赖已验收；重读最新 PRD/design/implement。
2. 确认本次“执行”授权与既定范围仍适用，再在主检出执行 task.py start <child>；不为同一已批准计划逐子任务重复询问。
3. 从 child task.json 检查 branch/worktree_path；切入该 worktree 后再写业务代码。
4. 记录实施时 HEAD、已有用户改动、迁移 heads、相关服务端口；不复用他人运行数据库。
5. 通过注入清单派发 trellis-implement/trellis-check；提示词以 Active task: <child path> 开头，限定所有权和不再嵌套派发。
6. 只读核验可共享主检出；所有业务变更与提交仅在该任务 worktree。不得 reset/rebase/force push 或改写 main。

## 开发与验证步骤

- [x] A：复现 422/500；实现来源与响应合同；补安全重试、并发确认、来源撤销和四开关组合测试。
- [x] C：把离线压缩复现转回归；预填同一个 manager；补自动触发、重复文本/消息 ID、取消及恢复。
- [x] B：冻结检索核心集和负例；实现词法计划/短词/分块/预算；测召回；再同步 citations/SSE/历史及 payload 限额。
- [x] D：添加 tombstone 防复活与唯一性回归；设计增量补建；覆盖重启、RAG-only maintenance、并发/撤销竞争；迁移检查。
- [x] E：逐项产出采用/延期理由、可执行评估和既有任务交接；MR-23 的完整模型候选→后续 core 路径不能用直接 upsert 代替。
- [ ] 每一步受影响 package 的 lint/type/test/build 按 AGENTS.md 执行，详见各 child implement.md。
- [ ] 协议有变化时执行实际后端+sidecar 合同联调；CI 可用本地受控 fake Provider，真实线上模型质量另测且不得复制家庭私有资料。
- [ ] 集成后执行 frontend-api-smoke；退出码 2 记录环境阻塞，不视为通过。（合并 main 后执行；本轮授权不含合并，见 execution.md 集成记录）
- [x] 仅在新变更、失败或未解决疑点时重复检查；审计已有 211 pass 不需为文档创建重跑。

## 集成与回滚

主检出为唯一串行集成点；每次集成检查 migration 冲突、接口兼容和最小充分回归。合并/部署不包含在本轮授权中。其他任务的管理端配置、家族树与 Steward 功能分支不纳入本提交。

回滚先禁用故障路径/新索引版本；保留来源、确认记录和 tombstone。不得通过恢复旧索引状态使已撤销内容可检索，也不得用复制 WAL 主库作备份。需要数据备份时使用 app.backup。破坏性降级和存量记录清理须给出可审阅报告后处理。

## 完成条件

父 AC-01～AC-10 各有结果/证据链接；一期修复已测试，E 的评估与延期边界已交付；剩余限制明确。未实施或尚未验证的事项保持未完成，不因文档齐全、测试 mock 为绿或任务归档而改称已修复。
