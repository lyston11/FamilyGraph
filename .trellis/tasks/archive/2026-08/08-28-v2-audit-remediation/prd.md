# FamilyGraph V2 审计整改与发布就绪

> 状态：Planning（2026-08-28 新建）。本任务承接审计，不代表整改已经开始或完成。  
> 审计代码基线：`dc46e34`。来源任务：`08-26-v2-agent-system` 及其七个归档子任务。  
> 发布原则：任一 P0/P1、隐私越权、Provider 失配、跨空间串线或不可复现验收存在时，状态不得写为 completed，也不得部署真实成员数据。

## Goal

把已经大体落地的 FamilyGraph v2 整改为“可安全运行、可复现验收、可审计交接”的发布候选版本。用户应能在自己的家庭/家族空间中使用 Assistant、关系解释、Steward、Memory/RAG 和受控联网，同时满足以下不可破坏的价值：

1. 结构化家谱事实、身份和空间权限始终由 FastAPI 领域服务掌握，Agent 只编排、解释和提出建议；
2. 每个用户看到的关系、称谓、记忆和推荐都严格受其当前空间、用途和字段级隐私策略约束；
3. 任何模型、网络、重试、缓存、SSE 断线或后台任务故障都不能造成静默写入、重复副作用、跨空间泄露或凭据外发；
4. 下一位开发者可以从任务工件和当前 commit 独立重跑验证，不依赖过时的口头 handoff。

## Background and confirmed facts

- v2.0–v2.6 均有真实代码提交，最新 HEAD 含 SourceFact 生产写入补丁；本任务不是重新规划双 Agent，而是修复已审计的实现和证据断层。
- 当前系统尚未部署，也没有真实成员、账号或业务数据。因此不需要生产迁移、双写、回填或旧客户端兼容窗口；所有新表/约束仍必须通过完整 Alembic 空库链。
- 既有全局合同仍有效：Account/Profile/Fact 三状态机、Household/Lineage 空间分离、四级 VisibilityPolicy、SourceFact 唯一真源、Assistant `account+session+space`、Steward `space+job`、用户确认门控、Pi Guard + FastAPI 二次授权、受控 Web 默认关闭。
- 审计证据与精确文件锚点见 `research/audit-baseline.md`；验证命令和证据格式见 `research/verification-protocol.md`。

## In scope requirements

### R-01 任务治理与证据可信度（G0/G1）

- 归档前后 `implement.jsonl`、`check.jsonl` 的路径必须可解析；验证器要覆盖活动目录、归档目录和历史自引用，不能因移动目录使上下文失效。
- 父/子任务的 `status`、PRD AC、implement 清单、handoff、notes 和实际 commit 必须一致。没有逐项证据的 AC 保持 `partial`/`blocked`，不得用“代码存在”代替验收。
- 任务元数据必须绑定实现 commit、验证 commit、残余风险和下一步；所有命令记录完整命令、退出码、测试数量和产物路径。
- 历史 v2 归档只做可追溯的追加式更正或审计附录，不删除原始决策，不把规划文档伪装成实现日志。

### R-02 原子建档与关系命令（F1）

- 新建他人时名字、与创建者关系必填；年龄等字段可空，描述保留原文、作者、时间和 scope。
- 后端提供一个原子领域命令/HTTP 入口，事务内完成 provisional profile、关系 SourceFact/请求、可选 space reference、审计和 domain event；任一步失败整体回滚。
- 代管 managed 档案可按既有 AD-4 规则直接建立关系；已有/已认领账号按 pending 合并请求流处理。不能由前端串接两个可独立成功的请求来模拟事务。
- 命令必须支持幂等键、重复提交、关系环/自环和权限重校验；Agent 未来只能调用该命令，不能复制 ORM 逻辑。

### R-03 VisibilityPolicy 与字段投影收口（F2/F3）

- 所有 profile、graph、search、statistics、export、attachment、Agent、RAG 出口都调用同一个 `VisibilityDecision(level, mask, purpose, policy_version)`；调用方不得只拿 visible ID 再读取原始字段。
- `purpose` 只能收紧不能放宽；统计只返回聚合所需的安全类别/桶化日期，不泄露 provisional、minor 或 lineage 摘要对象的精确生日、性别等高风险字段。
- `created_by` 的 custody 管理视图与普通 profile/agent/statistics 投影分离；代管人可管理必要字段，但不得绕过 provisional/minor overlay、凭据和高敏感字段红线。
- platform operator 仅看平台元数据；普通 admin API 不返回家庭姓名、性别、出生等资料。需要人工兜底时另走有原因、有审计的 break-glass 流程。

### R-04 数据权利与导出安全（F4）

- 导出、纠正、删除、争议状态机继续由领域命令掌握；删除/撤权先使查询和缓存失效，再异步清理 FTS、embedding、附件、DerivedFact、会话投影。
- 导出文件必须服务端加密、短期、一次性/签名下载，密钥和明文不进日志；过期、失败和 worker 崩溃均有清扫/恢复策略。
- 空库和合成数据验证导出权限、加密不可读、过期删除、撤销下载、审计及恢复后的完整性。

### R-05 ProviderGateway 与云/本地策略（R1）

- 平台配置的 Provider、密钥、allowlist、能力和 readiness 必须真实进入执行链；不能只把 `secret_ref` 传给 sidecar 后继续依赖未受治理的环境变量。
- 建立唯一 ProviderGateway（实现可在 FastAPI 侧或受控内部代理），统一解密、模型选择、流式/超时、错误映射、usage、审计和 secret redaction。
- `allowed / local_required / denied` 在入队和实际请求前均 fail-closed；本地不可用明确拒绝，绝不静默回落云端；每个 Run 固定 provider/model/policy_version。
- 首版仍只支持一个 OpenAI-compatible 云 Provider 与一个可选本地 Provider；不扩大多 Provider 路由范围。

### R-06 Internal API 与 sidecar 网络边界（R2）

- 浏览器只能访问公开 FastAPI API/SSE；internal listener/path 只能由 sidecar/内部网络访问，宿主和 nginx 不能直达。
- 保留 service token + run token 的 audience、typ、scope、space、job/run、allowlist 和 TTL 校验；默认开发 secret 不能成为生产防线，非开发环境必须拒绝默认值。
- sidecar 不挂载 DB/uploads，不拥有任意 SQL、shell、文件、任意 HTTP 或未审计 MCP；Provider egress 只能走获准网关。

### R-07 工具协议与 schema 一致性（R3）

- 后端 Pydantic/schema registry 是权威合同，sidecar TypeBox、前端类型和文档由同一版本快照同步；新增字段必须双侧同时改并有合同测试。
- 后端递归校验 required、type、min/max length、数值/数组范围、枚举、嵌套对象和 additionalProperties；未知工具/版本/字段一律拒绝并审计。
- 工具执行再次校验 run scope、VisibilityPolicy、幂等和领域命令；Pi Guard 放行不等于后端授权。

### R-08 Event、SSE、lease 与审计一致性（R4）

- `message.user_added` 只有一个权威写入点；每 Run `(run_id, seq)` 单调、幂等，事件先持久化再广播。
- reaper 对 `expired`/`cancelled` 写入可重放的终态公开事件并广播；Last-Event-ID 恢复不能漏终态、乱序或重跑工具副作用。
- 工具审计的 actor 语义统一为真实 user/account 映射，不能把 account 主键误写为 user 主键；错误 body、URL、Provider 诊断和 token 经过统一脱敏。
- 取消、超时、sidecar crash、重复 lease、重复 tool_call 和 provider failure 都有明确终态及可观察指标。

### R-09 Assistant、Steward、关系与称谓回归（A1/M1）

- Assistant 继续单空间、只读/确认分层；Steward 只能读取一个 `space_id` 的确认投影，不读私人 Session/Memory，不拥有 Web 工具。
- SourceFact、DerivedFact、TermRegistry、四级推断和用户原文保留规则不变；删除/争议/撤权后路径、称谓和推荐立即失效或进入可解释的缺失态。
- ActionCard 只产生建议；execute 时重新校验事实 revision、成员资格、目标空间、可见性和披露，不自动发申请、写 SourceFact 或合并空间。
- Memory/RAG 只索引确认且获准的内容；查询先做 scope/visibility/sensitivity/status 过滤，删除先 tombstone；Policy Guard 六个 hook 对输入、工具、结果、context、provider request、settled 全部 fail-closed。

### R-10 Controlled Web 与外部资料边界（W1）

- 受控联网默认关闭；只有平台、空间和本次 Run 均允许时才披露 `search_web`/`fetch_approved_page`。
- 保持 DNS/IP/redirect/port/content-type/size/timeout/approved-token/PII/secret/quota/citation 防线；外部内容标记 `trust=external`，不能成为 SourceFact、Memory 或 Steward 推荐依据。
- 重新执行真实跨进程 E2E，记录每条命令、退出码、SSE 历史恢复、带引用答案和 kill switch；不能只引用 handoff 摘要。

### R-11 前端移动端与缓存隔离（A1）

- 全局悬浮助手在桌面抽屉和 375px 移动全屏均完成核心旅程、键盘/屏幕阅读器/reduced-motion 验收。
- 空间切换、登出、账号切换、401/token_version、撤权和浏览器后退都关闭 stream 并清理敏感 store、草稿、消息、工具结果和 citation；不同 `(account_id, space_id)` 永不混用。

### R-12 空库部署、恢复和可运维性

- 从新数据卷应用完整迁移链，启动 api/web/agent 健康检查，执行 backup/restore、integrity_check、FTS rebuild、事件序列和关键表计数核验。
- graceful shutdown 停止新 lease、释放/恢复进行中任务；导出、Agent、RAG、Web 运行时故障不影响基础 v1 家谱 API。
- 所有残余风险、环境限制和 deferred 项进入任务结构化 notes，不以“非阻塞”掩盖未验证项。

## Out of scope

- 不做真实生产数据迁移、双写、回填或旧客户端兼容窗口。
- 不新增 MatchBroker、全平台陌生人推荐、多空间单 Session、物理合并家族空间、自动发申请或自动扩大公开范围。
- 不把 LLM 变成亲属图遍历、SourceFact 真源、权限判定或导出加密器；不引入任意 SQL、shell、文件、MCP 或 unrestricted HTTP。
- 不把所有聊天自动归档到 RAG，不采集键鼠/停留时长等泛行为，不把多人称谓使用晋升为全局模板。
- Provider 品牌、地区语言包和 embedding 具体实现可在不改变上述合同的前提下选择。

## Acceptance Criteria

> 所有 AC 初始为未完成；实施阶段逐条回写状态和证据。任何“部分完成”不得满足最终发布门禁。

- [ ] **AC-GOV-01**：新任务和历史归档的 JSONL 在活动/归档路径均能 `task.py validate`；不存在失效研究引用。
- [ ] **AC-GOV-02**：父/子任务状态、PRD AC、implement、handoff、notes、commit 互相一致；每条已勾选 AC 有 E2/E3 证据。
- [ ] **AC-FND-01**：名字+关系建档为单一原子命令；关系失败、重复提交、并发提交不会留下无关系档案或重复边。
- [ ] **AC-FND-02**：全出口字段级 VisibilityPolicy 通过主体×状态×空间×purpose 矩阵；统计、provisional custody、minor、operator 均无越权字段。
- [ ] **AC-FND-03**：导出加密、短期下载、过期/撤销/崩溃清扫和数据权利事件可验证；明文文件和明文日志扫描为零。
- [ ] **AC-RT-01**：数据库 Provider 配置驱动真实云/本地调用；secret 不经环境旁路泄露，readiness、usage、错误映射和审计可追踪。
- [ ] **AC-RT-02**：internal API 对宿主/nginx 不可达，service/run token 及默认 secret 防线通过负向测试。
- [ ] **AC-RT-03**：后端/sidecar/前端 schema 快照一致，所有约束和未知输入 fail-closed。
- [ ] **AC-RT-04**：Run 事件无重复 user_added；expired/cancelled 有持久化终态事件；SSE 断线重放不漏序、不重跑副作用。
- [ ] **AC-RT-05**：actor_id、错误、日志和审计字段语义正确且脱敏；PIN/JWT/Provider secret/PII 扫描无泄露。
- [ ] **AC-ISO-01**：Assistant、Steward、Memory/RAG、ActionCard、工具和缓存通过双用户双空间隔离测试；operator 不获得家庭数据。
- [ ] **AC-KI-01**：SourceFact/DerivedFact/TermRegistry/原文保留、删除传播和四级推断回归通过，LLM 更换不改变结构结论。
- [ ] **AC-ST-01**：Steward 单空间 job、ActionCard FSM、证据 revision、execute 重校验和推荐矩阵全绿；不自动写事实/发申请/合并空间。
- [ ] **AC-MR-01**：未确认内容不可检索，撤销/删除/tombstone 立即失效，Guard 六 hook 对 injection/PII/masked/local-required 全部 fail-closed。
- [ ] **AC-WEB-01**：受控 Web 默认关闭；SSRF、redirect、非文本、PII、配额、approved token、引用和 Steward 无 Web E2E 全绿。
- [ ] **AC-UI-01**：桌面与 375px 移动悬浮助手完成人工可访问性、错误恢复和空间切换缓存清理记录。
- [ ] **AC-OPS-01**：空库 Compose、迁移、健康、优雅停机、备份恢复、FTS rebuild、SSE 历史和全套质量命令均以当前 commit 可重跑并记录退出码。
- [ ] **AC-REL-01**：所有 P0/P1 关闭，P2 均有明确接受者/期限/缓解；Trellis 任务重新审阅后才可 `task.py archive`。

## Definition of done

1. 代码、测试和部署配置满足所有适用 AC；
2. `research/verification-protocol.md` 中的命令在隔离环境成功，证据达到要求等级；
3. 旧 v2 归档的矛盾状态有追加式审计更正，不能再误导后续 Agent；
4. `.trellis/spec/`、任务工件、实际实现和运行手册相互一致；
5. 任务 `task.json.status` 仍为 `in_progress` 直到最终检查；只有在完成门禁、绑定 commit、清理风险后才允许归档并形成 `completed`。

## Blocking open questions

无产品级阻塞问题。Provider 的具体品牌、加密库/密钥托管实现、internal listener 的进程拆分方式和首批地区语言包是技术选型，可在不改变本 PRD 合同的前提下由实现者记录选择与回滚方案。
