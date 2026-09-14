# Steward 候选相关证据版本

## 1. 适用范围

修改候选 assist 写回、候选证据模型、发布后的 candidate 交付，或建议/推测边的候选投影入口时读取本合同。证据版本是内部的历史核验记录，不是 SourceFact，也不代表新的用户确认任务。

## 2. 接口与存储

服务位于 `backend/app/services/steward_candidate_evidence.py`：

```python
is_internal_candidate(db: Session, candidate: StewardLlmCandidate) -> bool
record_for_candidate(
    db: Session, candidate: StewardLlmCandidate, *,
    batch: StewardAssistBatch, model_call: StewardModelCall, now: datetime,
) -> StewardCandidateEvidenceVersion | None
project_version(
    db: Session, *, candidate_id: int, version_id: int,
    job: StewardJob, now: datetime | None = None,
) -> bool
```

- `StewardLlmCandidate.attribution_status`：`legacy | unsupported | versioned`。原 `(space_id, candidate_digest)`、candidate ID、payload、首次来源 job 和驳回状态保持不变。
- `steward_candidate_evidence_versions`：`candidate_id`、`space_id`、`validation_contract_version`、`evidence_digest`、`support_facts_json`；首次来源 `source_job_id/source_batch_id/source_model_call_id`；`status`、`projection_job_id/projection_checked_at/invalidation_reason`、`created_at`。
- 唯一约束为 `(candidate_id, evidence_digest)`；pending 查询索引为 `(space_id, status, id)`。
- 迁移 `0049_steward_candidate_evidence` 以 `0048_steward_terminology_publication` 为父节点。候选新增列采用原地 ALTER，不重建已有父表。
- 不新增 API、模型输出字段、环境开关、交付 kind 或维护队列。

## 3. 必需行为

**相关支撑与归因。** `common-biological-parent-v1` 仅支持 `direct_sibling(A,B)`：收集所有完整的 confirmed `biological_parent(P,A)` 与 `biological_parent(P,B)` 配对。排除单边、无关、收养、继亲及监护事实；A/B/P 均须符合现有 Steward 的 active member/ref/owner 内部空间范围，事实属于当前空间或全局。此范围不替代个人读取授权。

快照按 fact ID 排序，仅含 `id/revision/fact_type/subject_user_id/object_user_id/space_id`。digest 来自合同版本与该相关快照，不含姓名、模型自由文本或全空间 hash。既有 assist 的全空间输入 hash 继续独立承担请求竞争校验。SourceFact 没有 TTL；有效性取决于当前 state、原 revision、结构与范围。

**稳定身份与内部隔离。** 历史候选迁移为 `legacy`，没有伪造证书。未能归因的输出为 `unsupported`；得到证书后进入粘性的 `versioned`，支撑失效也不能降回公开模式。同空间 sibling 的两个方向共享内部隔离，保留两个历史 ID，不合并或重排。已有反向 legacy 行与后来无证书的反向输出均须受约束。建议与推测边入口分别调用 `is_internal_candidate`，包括已准备的旧公开意图。

**写回与一次核验。** assist 保留现有输入、Provider、预算和 lease/attempt 栅栏；候选复用、模式标记、版本插入及 batch 应用处于同一写事务。唯一性冲突只复用旧版本，不覆盖其首次来源或快照。后续 core 的只读 `prepare_intents` 捕获 pending 版本，包括 dismissed 候选；每个版本准备一个 `kind=candidate`、`key=candidate:evidence:<version_id>`、payload 为 `{candidate_id, evidence_version_id}` 的意图。准备后新增版本留待下一次 core。

assist 的应用事务必须在获得 SQLite writer 后重新采样时间；`max(caller_now, live_now)` 允许测试/恢复调用方收紧检查，不能用入锁前的旧时间延长租约。取锁等待跨过 deadline 时保留结果供恢复处理，旧执行者不应用证据。

发布后在原 generation/input/claim 栅栏内，先分流内部意图，再加载任何公开投影的全空间 facts。`project_version` 只查询保存的支撑 ID 和对应节点范围，将该 pending 版本一次转为 `projected` 或 `invalidated`，与意图回执同事务提交。返回值表示本次是否记录了终态；已终态或不匹配的版本返回 false。`projected` 仅说明所记录时点核验通过，后来来源变化不改写旧核验。

**保留用户历史。** 内部路径不得调用建议或推测边 upsert；`notify=False` 仍可能新建 recipient，不能作为内部化手段。不得新建、复活或覆盖既有 Suggestion、recipient、Notification、推测边、确认及驳回历史；共同父母证书不得计入 `_evidence_summary` 的已确认单跳关系计数。已可派生的称谓继续由 PFV/Terms 自动处理。

**不可变性与删除。** DB trigger 阻止快照、首次来源替换及终态核验改写，并阻止 `versioned` 回退。来源 job/batch/call 和投影 job 删除可通过 `SET NULL` 清空引用；candidate/space 删除按领域 CASCADE。候选到首次 job 的既有 CASCADE 仍保留，未新增生产 job GC。

## 4. 校验与错误矩阵

| 条件 | 结果 |
| --- | --- |
| 相同相关事实集跨 job/重试 | 复用同一版本，首次来源、快照和终态不变 |
| 只新增无关或单边事实 | 不新增证据版本 |
| 不支持的候选或无完整配对 | 不生成证书；保持/继承内部模式，否则标记 unsupported |
| 写回来源的 space/job/batch/kind/status 不匹配 | 抛出 provenance mismatch，不采用输出 |
| 未知合同、候选结构改变或畸形快照 | invalidated；分别记录 `unsupported_contract`、`candidate_structure_changed`、`invalid_support_snapshot` |
| 来源缺失、非 confirmed、revision/空间/结构改变 | invalidated；记录 `source_missing`、`source_not_confirmed`、`source_revision_changed`、`source_scope_changed` 或 `source_structure_changed` |
| 端点或父母不再属于内部输入范围 | invalidated，`source_out_of_scope` |
| batch 或 delivery 租约/attempt/input fence 失效 | 由原执行器拒绝旧写回，不越过 fence 核验版本 |
| 修改不可变快照、替换来源或改写终态 | SQLite 拒绝；FK 清空引用仍允许 |
| 降级将丢失版本或 adopted attribution | 首个 DDL 前拒绝，保留数据并向前修复 |

0049 的 downgrade 须从自身 revision 解析计划，将计划传入 0048 的 `_preflight_parent_downgrade(planned=...)`，复用祖先拒绝条件。`-1` 可退回 0048 的空新结构；当前 merge 图的 `-2` 歧义须在 DDL 前拒绝；深降级不能先拆本层 schema 再被祖先拒绝。

## 5. 基线、正例与禁止行为

- 基线：旧候选、旧建议及其确认/驳回经过 upgrade 后逐字段不变，归因仅初始化为 legacy。
- 正例：已忽略候选后来得到完整共同父母事实，记录内部 pending 版本；后续 core 核验，原忽略仍有效，公开记录总数不增加。
- 正例：增加另一完整共同父母配对产生新版本；旧意图只消费原先捕获的 ID，新版本保持 pending。
- 禁止：用整个空间 evidence hash 作为版本身份；用模型自报依据作证；换版覆盖旧 payload/来源；来源失效后以反向候选绕过内部模式；把 projected 当作已确认关系。

## 6. 必需验证

- 真实迁移临时库上的 core → delivery 注册 → fake transport assist → 后续 core/delivery；确认相关事实换版、无关/无新事实及已应用 batch 重试不重复。
- 两条主链：未完整支撑时真实公开投影/私人及共享 dismiss 后补齐事实；初次就有完整支撑且从未产生公开投影。断言原候选、建议、recipient、Notification、推测边与确认历史保全。
- 捕获版本 ID 与真正输入变化分别构造，防止版本测试被 input fence 短路；覆盖原 revision、端点/父母失权、撤销、跨空间来源、stale batch/delivery lease。
- 两个真实执行者的唯一性与原来源保全；DB 快照/终态不可变和 `SET NULL` 清理。
- FK ON/OFF 的旧数据 upgrade、单一 head、无损 `-1` 往返、歧义 `-2` 和绝对深降级拒绝。拒绝前 DDL 计数为零，schema/head/data 不变；既有 RAG/Memory/0048 迁移测试继续通过。
- 完整后端 Ruff、format、mypy 与 pytest；真实模型质量、生产迁移/开关及部署另行验收。

核心回归位于 `backend/tests/test_steward_candidate_evidence.py`、`test_steward_candidate_evidence_integration.py` 和 `test_steward_candidate_evidence_migration.py`。在 `backend/` 执行：

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_steward_candidate_evidence*.py
```

## 7. 错误与正确的交付

```python
# 错误：旧意图消费了准备后产生的新版本，且 notify=False 仍会写 recipient。
for version in all_pending_versions(candidate_id):
    upsert_suggestion(..., notify=False)

# 正确：已通过原 publication/input/claim fence 的内部意图只核验捕获版本。
project_version(
    session,
    candidate_id=payload["candidate_id"],
    version_id=payload["evidence_version_id"],
    job=job,
)
```

关联合同：[Steward 发布与交付](steward-action-card.md)、[事实与称谓](relationship-intelligence.md)、[数据库事务](database-guidelines.md)。行为重建的独立键族合同见 [steward-behavior-rebuild.md](steward-behavior-rebuild.md)。
