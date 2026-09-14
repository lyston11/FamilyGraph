# B 引用协议前置核验与接口草案

日期：2026-09-13。只读基线：A 提交 `d1f43a58918e100226f3212cb3eabd6a14c4ac40`，worktree `/private/tmp/familygraph-memory-rag/09-13-memory-contract-repair`。本文依据本任务 PRD/design/dispatch-boundaries 和该提交的实际后端代码；不重审 A，不代表 B 已实现。未改业务/测试，未执行测试或模型调用。

结论：现有结构足以承载 B，需补“精确片段定位、attempt 内 build 唯一性、原请求指纹、读取时统一授权”四条连接。A 的根来源授权可以直接复用，但不能单凭现有 `document_readable` 认证某个历史片段。以下字段/函数名为待主线程与实现者统一的草案。

## 1. 决定接口的现状

| 实际代码 | 结论与影响 |
|---|---|
| `backend/app/models/context.py:14` `ContextBuild` | 只有 run 与时间索引，无 attempt/唯一性/失效标记；旧多次 GET 可以留下多个 build，不能从最新一行推断有效执行。 |
| `backend/app/services/context_builder.py:38` `ContextSource.from_hit`；`:192` item metadata | 丢弃了 RAGHit 的 document/chunk/index_version；item 仅存 scope/sensitivity/revision，无法独立校验旧片段及 hash。 |
| `backend/app/services/agent_tokens.py:27,66,113` | run token 尚无 attempt。`internal_agent.py:203` 的 DB scope 比较也没有 run/job attempt；不能把 LeaseOut 已含 attempt 视为 token 已绑定。 |
| `backend/app/services/agent_queue.py:300,308` `lease_next` | 每次 lease 在同一立即事务中递增 job.attempt，并同步 run.attempt；这是可复用的执行身份，续租时间不是身份。 |
| `backend/app/services/agent_events.py:134,144,193` | seq 在取得 writer 前读取；重放直接比较持久 payload；assistant 历史只保存 text/web_citations。认证后 payload 与原请求不同会破坏现有重放比较。 |
| `backend/app/api/agent.py:121,428,495,519` | 历史直返 content_json；SSE 查询关闭短 Session 后才 `_wire_event` 直返 public_payload。新增引用不能只在写入时检查。 |
| `backend/app/api/internal_agent.py:485` | 给 sidecar 的历史也直接传 content_json；新增私有引用定位后，此出口必须过滤，不能只改浏览器历史。 |

## 2. 最小字段与 wire

### 持久模型

| 对象 | 建议增量 | 兼容原则 |
|---|---|---|
| ContextBuild | `attempt: int | None`、`invalidated_at: datetime | None`、有界 `invalidation_reason`；唯一 `(run_id, attempt)` | 旧行 attempt=NULL 保留；新行 attempt≥1。失效后也占用本 attempt 的唯一 build，不偷偷重建。 |
| ContextBuildItem.metadata_json | 增加版本化的 ExactChunkRef；保留 scope/sensitivity/revision/rank/included | 复用现有 JSON，优先不新增片段外键。旧 metadata 不伪造为精确来源。 |
| AgentRunEvent | `request_fingerprint: str | None`（64 hex；摘要输入含协议版本） | 旧行 NULL，继续受限旧比较路径；不从已加工 payload 反推原请求。 |
| AgentMessage.content_json | 完整、有界、服务端认证的 citations 及每项私有精确定位 | 与 event/fingerprint 同事务写入。公开投影不返回私有 hash、build/attempt 或内部引用结构。 |

建议 persisted citation 保持现有六字段，并附一个仅服务端读取的 `_source_ref`；由统一 projector 生成对外 citations。这样不用添加 Message 列，也不会在 Run 清理、ContextBuild 级联删除后失去历史来源的精确定位。字段编码最终以 wire contract 为准。

run token：`issue_run_token(..., attempt: int)` 必传 lease 取得的值，decode 要求整数且 ≥1（bool 不是合法整数）。`_authorize_run` 比较 `claims.attempt == run.attempt == job.attempt`，保留现有 scope、成员资格及各入口状态门禁。旧 token 缺 attempt 拒绝，禁止默补当前值。

ContextOut 已有 `attempt`/`context_build_id`（`schemas/agent.py:96,102`），无需再造名称；sidecar 必须保留并原样用于候选引用。`context_blocks` 继续用现有 citation/content；精确 hash 不必发给 sidecar。

```typescript
// 新增在 EventIn 上；与 public_payload 分离，extra 字段拒绝。
type ContextReference = {
  build_id: number
  attempt: number
  used_handles: string[]
}

type EventIn = {
  seq: number
  type: string
  public_payload: Record<string, unknown>
  context_reference?: ContextReference
}

// 公开 citations 仍满足现有六字段；无正文摘录。
type Citation = {
  source_type: string
  source_id: string
  scope: string
  sensitivity: 'normal' | 'sensitive' | 'high' | 'local_required'
  revision: number
  citation_handle: string
  document_id?: number
  chunk_id?: number
  index_version?: string
}
type CitationProjection = {
  citations: Citation[]
  unavailable_citation_count: number
}
```

只允许 `message.assistant_added` 携带 context_reference；它来自最终回答使用的句柄，不来自材料列表。handle 只是 opaque locator，后端按 included item 精确匹配，不解析字符串取 source_id、不信任 sidecar 自报的来源字段。事件输入不得直接写可信 citations/私有 `_source_ref`/不可用计数；这些保留字段由服务端生成。旧 sidecar 不带 reference 时仍可提交正常文字与 web_citations。

建议首版与 ContextBuilder 共同固定 `MAX_INCLUDED_SOURCES = MAX_USED_HANDLES = 20`（当前 builder 调用的检索默认 limit 为 20，`memory_rag.py:793`），句柄 ≤255、source_id ≤255、index_version ≤32；若检索方调整上限，必须同步 schema/预算/测试。过长身份字段不能截短后充当另一来源。

## 3. 精确来源 helper：A 可复用什么

`memory_sources.py:313` 的 `document_readable` 已覆盖根 Memory 生命周期/legacy、来源依赖链、scope、作者可见性、读者身份和空间成员资格。其内部链路在 `:222`；**它只接受 document，不能验证某个 chunk 与 build 当时用过的片段相同**。

A 的 `_source_documents:192` 有精确 ID/version/hash 验证，但输入必须是 Memory/Candidate，并包含保存记忆的语义；`resolve_source:448` 同样是保存入口。不要伪造 Memory 对象或调用创建候选来认证引用。

建议由“检索与上下文”责任方在来源模块提供唯一的小接口，后端协议责任方只导入：

```python
@dataclass(frozen=True)
class ExactChunkRef:
    document_id: int
    chunk_id: int
    source_type: str
    source_id: str
    revision: int
    index_version: str
    quote_sha256: str

def read_exact_chunk(
    db, ref: ExactChunkRef, *, actor, account, space_id,
    agent_kind="assistant", for_model=False, provider_kind=None,
    require_active_index=False,
) -> AuthorizedChunk | None: ...
```

- 精确读取 document/chunk，检查关联、source type/id、document 与 chunk 的 source revision、chunk version/status、当前文本 SHA-256；再调用 A 的 `document_readable`，不以全文搜索命中证明授权。
- AuthorizedChunk 返回当前规范的 scope/sensitivity/space/作者和受权正文。build metadata 另保留这些已有快照值，重复 context 时检查当前策略是否仍允许；不得相信消息里旧 scope 代替授权。
- 新检索要求活动 index_version；旧 build/citation 用 `require_active_index=False` 精确解析原 chunk，不能静默换成新版块。是否仍有效依来源生命周期，不依算法版本是否最新。D 的 tombstone/版本存储合同继续有效。
- `for_model=True` 时保留 high/local_required 的本地 Provider 要求；浏览器历史/fallback 只做人类读取授权，不能因为没有 Provider 选择而拒绝本来可读的来源。
- ContextSource.from_hit 要携带该 ref，ContextBuildItem 持久化 ref；raw_quote 不因此加入全文索引。

## 4. 同 attempt 的 build 与事件事务

### Context：锁后确认唯一 build

建议给 `ContextBuilder.build` 增加来自签名 claims 的 `attempt`，不从之后刷新的 `run.attempt` 重新取一个值充当请求身份。

1. internal `run_context:425` 初步验证 token；在读取/创建 build **之前**取得 SQLite writer，刷新 run/job 并复验签名 attempt、scope、成员资格及现有状态门禁。可复用无副作用条件 UPDATE 的模式；不要先写 build 再判断谁赢。
2. 查 `(run_id, attempt)`。无记录才做有界检索/预算/策略校验并插入一次；有记录只依 item ref 重建当前仍可读的原块，保持原 included 集及顺序，不重新执行搜索选一个新集合。
3. 重复 GET 复核 query_hash、既定 budget/policy 和当前 RAG/Provider 有效策略。原 included 来源失效时标记该 build invalidated，提交该安全状态，再返回明确 409 `context_invalidated`；以后同 attempt 仍不能生成新 build。异常自动 rollback 不能意外撤掉这个持久失效标记。
4. DTO/策略检查后一起提交 build+items；只有已通过最终 context hook 的实际下发材料算 included。目前 `policy_guard.context_hook:224` 是整体拒绝或复制通过，不会逐块静默裁剪。

锁内只含有界 SQLite 工作，无模型调用。RAG 关闭时可以保留空 build；同 attempt 后来开启不自动追加新材料，新 attempt 再建。

### Event：原请求指纹先裁决重放

```text
身份/签名attempt门禁 → 取得writer并复验 → 读prior及next_seq
  prior有指纹且同type/原请求指纹：返回原 event_id/seq，不再认证来源或改写持久数据
  prior无指纹：仅接受无新reference且旧type/raw payload逐值相同的兼容重放
  prior异参：409
  新event：连续seq → 认证build/included/精确来源 → 构造公共payload和完整Message
           → DTO/UTF-8上限 → event+Message+fingerprint同事务提交
```

指纹建议为 SHA-256(canonical JSON `{v,run_id,attempt,seq,type,public_payload,context_reference}`)，键排序、固定 Unicode/JSON 编码、拒绝 NaN，**在任何服务端认证/裁剪之前**计算。重试始终使用原输入；不要对已认证结果重算。used_handles 是否去重排序作为规范化的一部分必须预先固定。

`agent_events.append_events:120` 应接收不可变的 `expected_attempt` 和可信 actor/account/space，内部不能自行把当前 run.attempt 当作请求值。queue 内部 `insert_event:83` 用于首事件/终态，继续保留其内部入口，不要求虚构 sidecar reference。

合法重试遇到**来源**撤权仍确认原提交，只返回已有 EventAppendOut 中的 ID/seq/duplicates（`schemas/agent.py:125`），不返回旧引用内容。账户/会话成员资格失效、旧 token/attempt 或终态等既有外层门禁仍可拒绝，不把“来源变化不影响重放”扩大成绕过身份/状态。混合批次的任一新事件失败要按 `internal_agent.py:541` 回滚半批次，再写审计。

## 5. signed attempt 必须到达现有 admission 点

仅 `_authorize_run` 初次比较不足：请求可能在鉴权后暂停，队列重新 lease 后再恢复。主线程已同意下表有限补点归 B 后端协议责任方；只加 expected_attempt 传参/锁内复验与回归，不改队列状态机、取消或已放行请求语义。

| 调用方 → 实际写入点 | 最小补点 |
|---|---|
| `internal_agent.py:295` lease → `agent_tokens.issue_run_token:66` | 签入 `grant.job.attempt`，要求 run/job 一致。 |
| `internal_agent.py:425` context → `ContextBuilder.build:82` | claims attempt 传入；build 查询和最终授权前取得 writer，刷新双实体。 |
| `internal_agent.py:521,540` append → `agent_events.append_events:120` | claims attempt 固定传入；锁后重验再读 seq/prior，避免签名正确但旧执行在新 lease 后写入。 |
| `internal_agent.py:332,338` heartbeat → `agent_queue.heartbeat:317` | 加 `expected_attempt`；在其既有 `_immediate_tx`（`:320`）内刷新 job/run 并比较，再更新心跳。不能在外层先持 writer；该事务要求 clean Session（`agent_queue.py:60`）。 |
| `internal_agent.py:605,610` settle → `agent_queue.settle_run:343` → `_settle:373` | expected_attempt 透传到 `_settle` 既有锁（`:382`），锁内复验再落终态。浏览器取消走同一 `_settle`，其内部调用不伪造 token attempt；保持原状态机。 |
| `internal_agent.py:571,580` tool → `agent_tools.execute:407` | claims 已传入；给现有 admission 条件 UPDATE（`:523`）加签名 attempt 条件，保留它 `:537` 的短提交边界。新 attempt 合法重放既有 `(run_id,tool_call_id)` 仍遵守原副作用去重，不将 key 改为按 attempt 重做。 |
| `internal_agent.py:365,387` provider → `provider_proxy.stream_provider_response` → `_admit_upstream_request:113`（调用在 `:273`） | 透传 expected_attempt，加入现有发 socket 前 CAS（`:127`），`:137` 提交后才 I/O。不能将数据库 writer 持有到模型流结束。 |

工具不是全部只读：`agent_tools.py:541` 有 AgentToolCall 持久占位/结果；`:648` 的 `record_term_usage` 调 `terms.record_usage_and_promote` 写业务记录。Provider 在 `provider_proxy.py:274` 真正 `client.send` 并写 egress 审计。这是已有可观察副作用入口的静态证据，非假设未来工具会写入。

Provider/工具通过 admission 后已在途的请求继续遵守现有规则；B 不承诺撤回已发送字节。不将普通历史/SSE/fallback 读取都变成写事务。

## 6. 一个 projector 服务三个读取面

建议后端协议责任方提供 `project_stored_citations(db, stored, *, actor, account, space_id) -> CitationProjection`，只通过精确 resolver 生成完整可读项，失效项仅增加 unavailable 计数；无 legacy 精确证据不补猜。返回新字典，不修改持久 JSON 或全局来源状态。

- **历史**：`api/agent.py:_message_out:121` 改接 db+可信身份+会话 space，`list_agent_messages:415` 等调用跟进。`internal_agent.run_context:485` 对历史也过滤私有定位并重授权，或在该出口明确只保留其契约允许的文字，避免遗漏。
- **SSE**：`_event_stream:533` 保存身份 ID，不捕获 ORM/请求 Session；`_fetch_new_events:495` 在每次短 Session 内重载身份、验证本人 Run、投影引用并生成 DTO/bytes，关闭 Session 后仅发送纯值。来源失效仍发送正文和可用引用/计数；整个读取主体或会话无权时结束连接，不造新的公开事件类型。
- **fallback**：新增 `GET /api/agent/runs/{run_id}/events/{seq}/citations`。先复用本人 Run/会话门禁，以服务端键 `run:{run_id}:event:{seq}`（`agent_events.py:201`）并限定 session+assistant role 查 Message，校验对应事件是 assistant_added，再调用同一 projector；不能把任意 message ID 当入口。既有事件但无引用/无旧 Message 投影返回空集合；无权/不存在目标按既有防枚举 404。
- **旧主体语义**：当前 `_own_session_or_404:103`/`_own_run_or_404:110` 仅保证本人所有权，并未在历史读取额外要求当前空间成员。B 的来源 projector 必须按当前成员资格遮罩引用；不在这个任务里暗改本人历史文字的既有保留语义。

SSE 若新增有界批次，必须先排空 cursor 后的全部批次，再按 Run 终态关闭：不能保留当前 `:546` 的“处理一批就检查 terminal”写法，否则终态 Run 的后半段事件会丢失。无需在本草案中强制新增分页；这里标明改动时的具体风险。

### 16 KiB 与完整引用

统一按实际 serializer 计算**整个 public_payload** 的 UTF-8 字节，遵守 `agent_events.py:60,113` 的既有限制。先验证原 role/text/web_citations 本身可持久化，再按稳定顺序装入能放下的引用；不截正文，不把字节上限当字符数。完整引用及精确定位只在 Message 持久化。

正文占满时连计数字段也可能放不下，因此 fallback 不能依赖“必须存在一个提示字段”。最小可行方式是前端对每个 assistant_added 按已有 run_id/seq 补取一次；若采用“完整标志存在时跳过补取”的优化，缺标志必须回退。补取失败保留正文并允许重试。历史直接走完整 Message 投影，SSE 内联只是有界便捷副本。

引用失权隐藏的是结构化来源元数据/摘录；本任务不把历史 Assistant 已写进正文的事实承诺为物理擦除，沿用父合同。

## 7. 旧数据迁移与实现前待收敛项

- 首选 additive 列/索引，保全旧 ContextBuild/Items/events/messages；旧 build attempt 一律 NULL，不按当前 Run.attempt 或 created_at 猜填。SQLite 唯一 `(run_id,attempt)` 允许保留多个 NULL 历史行。
- event fingerprint 旧行保留 NULL，不以新算法伪造“原请求摘要”；未知引用来源按不可认证处理，保留原持久资料。新 token 缺 attempt 直接拒绝；部署时排空/重新 lease 旧任务的方案只记录，不在本次静态核验操作运行队列。
- 如决定为 item 加 `(build_id,citation_handle)` 唯一索引，先报告实际重复组；不能自动挑一条删除。也可在新 build 构造时保证唯一、认证读取时遇歧义拒绝，暂不为旧数据额外增约束。
- 组合迁移唯一所有者为协议责任方，等检索方精确字段/索引设计稳定后基于真实 Alembic head 排号；D 的版本/tombstone迁移不可各写一套。

以下属于实施前的常规协议取舍，尚非已确认业务缺陷：

| 待收敛项 | 草案建议/不可变边界 |
|---|---|
| 新事件含无效或刚撤权的 reference | 错 run/attempt/build/未 included 的结构性伪造拒绝；来源在生成期间失效时，选择 409 context_invalidated，或保留文字并以不可用计数呈现。两种都不得认证/输出失权元数据；worker 的最终错误/重试行为须同步。 |
| citation 内部存储编码与有限上限 | `_source_ref` 只在服务端，公开仍为六字段完整项；明确 20 项或检索方最终上限，避免三端各自决定。 |
| context_invalidated 如何推动下一 attempt | 使用既有队列重领机制/明确失败，不在 context GET 中偷偷递增 attempt 或绕过状态机。当前 worker 如何收到该语义由主线程与 sidecar 实现者统一。 |
| 原请求规范化和旧事件比较 | 固定一个版本的 canonical JSON；旧 NULL fingerprint 只走严格旧输入等值路径，不默认任何新请求都是重放。 |

## 8. 核验边界与交接

本报告只读检查了上述后端源码、关键模型/schema、现有 queue/admission 实现和相关测试名字/关键片段；未执行测试、lint、type-check、迁移或网络调用。引用的事实以冻结 A 提交为准，C 的当前实施文件没有读取或改动。

后续应以受控同步点覆盖：context 双 GET 只建一个 build；旧请求停在鉴权后、新 lease 推进再恢复；认证后丢响应及来源撤权后的同请求重放；新/旧 chunk hash不同；SSE 断线重放与短 Session 中途权限变化；满字节正文+完整 metadata fallback；旧 NULL attempt/fingerprint 保全。它们是交给 B 的验收要求，本文不记为已通过。
