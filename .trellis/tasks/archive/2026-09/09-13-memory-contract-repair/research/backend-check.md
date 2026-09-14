# A 后端独立检查

日期：2026-09-13。检查者：`/root/memory_backend_check`。任务基线 HEAD：`20d03084df341f6cc5fc8fcc18042757c07d822a`；对象为 A worktree 的本轮未提交代码，包括检查期间实现者补入的局部修复。行号以本报告写入时的版本为准。

结论：本次检查发现并独立复现了一个 legacy 来源恢复的范围校验缺口；实现者已修复，同一隔离探针由失败变为通过。下述已读 A 后端范围内，未发现其他待修业务阻塞。最终包级门禁、完整 A 新测试和存量迁移矩阵仍由主线程/实现者统一完成；本报告不替代它们。

## 权限与范围

- 工作目录：`/private/tmp/familygraph-memory-rag/09-13-memory-contract-repair`。
- 只读业务代码和测试；仅写本报告及 `/private/tmp` 独立探针/工具缓存。未修改业务文件、测试、迁移、Git 状态或主检出。
- 已加载注入的完整上下文文件、`check.jsonl` 及其四个输入、任务 PRD/design/implement、`research/api-contract.md`。`.trellis/spec` 索引自标历史资料，未将其当作新的实现门禁。
- 逐段检查 `models/memory.py`、`schemas/memory.py`、`api/memory.py`、`services/memory_sources.py`、`services/memory_rag.py`、`services/rag.py`/`memory.py`、`0042_memory_source_contract.py`；追读数据库会话、授权辅助函数、相关 Agent 模型和现有测试夹具。
- 抽查新增 `test_memory_api_contract.py` 的序列化回滚、来源失效读取面、legacy 恢复、并发和 writer 后授权复验测试；实现者仍在补充测试，不重复运行其测试集。

## 已修问题

### A-CHECK-01：legacy 恢复可越过迁移的跨空间隔离

原位置：`backend/app/services/memory_sources.py:608` 的 `verify_legacy_source`；修复后为 `:610`，新增范围验证在 `:624`，拒绝在 `:639`，真正写入在 `:642`。

触发：旧 Memory 指向本人空间 A 的原始 user 消息，但既存 scope 为 `household:B`。这种数据在新迁移中会保持 unverified：`backend/migrations/versions/0042_memory_source_contract.py:115` 明确拒绝跨来源空间升级。原恢复 helper 只验证消息归属和原文，随后直接 `apply_source`，未检查 Memory 已保存的 scope。

独立探针以真实 Alembic head 建隔离库，建立上述旧记录，调用真实 helper → `index_memory` → B 空间另一成员的真实 `search_rag`。原结果为：

```json
{"adapter_recovered":true,"verification":"verified","reader_access":"available","other_space_rag_hits":1}
```

断言不可读失败，`1 failed in 1.10s`。这证明恢复入口能够绕过本轮迁移的保守隔离判断；不只是字段值异常。在 `backend/app` 和当时的 `backend/tests/test_memory*` 检索 `verify_legacy_source`，未发现生产 HTTP 自动调用；问题位于本次提供的来源恢复契约入口。

实现者收到报告后，在 `apply_source` 之前将既存 Memory scope 与 `_scope_options`、原 user 所属空间或 RAG 根链允许范围求交，并拒绝高敏感共享等不允许的恢复。检查者沿源码确认拒绝发生在任何来源字段修改前。同一探针复跑结果为：

```json
{"adapter_recovered":false,"verification":"unverified","reader_access":"unverified","other_space_rag_hits":0}
```

`1 passed in 1.09s`。探针使用合成文本与账户；未触碰开发/线上数据库。

临时探针：`/private/tmp/familygraph-memory-rag/test_backend_review_probe.py`。在 A 的 `backend/` 中执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests .venv/bin/python -m pytest -p conftest -p no:cacheprovider -q -s /private/tmp/familygraph-memory-rag/test_backend_review_probe.py
```

复用 `tests/conftest.py` 的隔离 DATA_DIR 和真实迁移 fixture，仅执行这个新探针。已建议实现者将该负例加入持久回归测试。

### A-CHECK-02：显式 DTO 构造的类型错误与格式

首次 `mypy app` 报告 `backend/app/api/memory.py:38,47,69,78` 共四个 `str` → `Literal` 参数错误。首次格式检查还提示 `memory_sources.py`。

实现者改为明确的字段字典 → Pydantic `model_validate`，保留运行时 Literal 验证，未使用类型忽略或绕过 DTO 校验。修复后源码位置：`backend/app/api/memory.py:33`、`:65`。格式修复也已通知完成。检查者已抽查代码；按主线程要求，最终 lint/format/mypy 统一复核，不在这里重复全包运行。

首次全包 lint 另有旧 `0041_term_pack_expansion.py:109` 的 E501/格式问题。主线程报告已在 A worktree 做 AST 不变的机械格式修复，并确认结果与主检出现有格式改动逐字一致；本检查者未改该迁移，也未将其记为 A 新增业务错误。

## 关键代码结论

| 合同 | 实际路径与结论 |
|---|---|
| 不推断 manual | `memory_sources.py:93` 的 `normalize_source` 拒绝无来源、冲突新旧字段及非空客户端 source_span；只适配可解析定位。`resolve_source:448` 继续回读授权，字符串不是凭证。 |
| 独立 user 快照 | `original_user_text:84` 仅接受 user 且 JSON 恰为 text；`resolve_source:481` 校验本人会话、成员资格和全文相等。`_source_documents:169` 仅对已确认独立快照跳过 live chat，确认过程以 `require_live_message=True` 重验；不会把删除消息变成 manual。 |
| 根来源复制 | `_source_documents:192` 校验 document/chunk/revision/version/hash/来源身份；`_document_chain:222` 重验 Memory 生命周期、版本、scope、敏感度并设环/深度限制。`source_access:371` 与 `document_readable:313` 再做当前读者、原空间与作者可见性检查。 |
| 后台与读者分离 | `source_lifecycle:258`/`memory_materializable:268` 不把单个读者失权变成全局来源无效；`memory_access:426` 叠加具体读者与目标空间权限。`index_memory:604` 和 `rebuild_index:1119` 使用物化守卫。 |
| 管理读取遮罩 | `api/memory.py:29,56` 的 mapper 对不可用/未验证来源隐藏 raw_quote、summary/content、purpose 和来源定位；`list_memories:197` 仅让所有者看到受限元数据。已确认主线程先前指出的 source_message_id 遮罩修复存在。 |
| 检索及模型 | `memory_rag.py:849` 排除未验证 Memory，`:885` 使用完整来源可读性；`rag.py:42` 为人工阅读设置 `for_model=False`，不会放宽模型外发的 Provider 限制。 |
| 创建幂等 | `memory_rag.py:224` 对实际影响结果的规范化输入取指纹；`:280` 用唯一键冲突处理，`:330` 重放还重验来源读取。模型/迁移均有 `(author_account_id,idempotency_key)` 唯一性。 |
| 确认竞争 | `memory_rag.py:451` 对 pending 做条件 UPDATE；Memory 的 `source_candidate_id` 有唯一索引。writer 获取后 `:474` 再核来源、`:479` 再核目标 scope、`:481` 刷新有效开关。确认指纹保留 retention_days 原参数，`:534` 重放校验 Memory 活性与当前授权。 |
| 提交前响应验证 | `api/memory.py:114,151,165,218` 等均先构造 DTO/`model_dump_json()`，再 commit；返回 DTO 不依赖提交后 ORM 属性读取。204 删除无动态响应体。 |

上述否定结论仅针对表中已追读路径；未声称完整项目或 B/C/D 的功能已通过。

## 迁移与数据保全

- 实际迁移从 `0041_term_pack_expansion` 接到 `0042_memory_source_contract`；独立探针的真实迁移 fixture 两次均成功运行到该 head。
- `0042:154` 在改表前检查重复确认，发现重复立即报告，未选择任意一条或删除其他记录。
- `0042:166` 至 `:225` 在 savepoint 中暂存 Memory 的原列，先移除子表再重建候选表，再按旧字段恢复 Memory，避免父表重建触发 `source_candidate_id SET NULL`。异常显式回滚 savepoint，完成前执行 `PRAGMA foreign_key_check`。
- 检索 `backend/app/models` 与全部现有 `backend/migrations/versions/00*.py`：除 Memory 自身指向候选外，未发现其他需要在此次父表重建中保全的 Memory/Candidate 外键；未发现 Memory 表自有业务 trigger 被这次重建漏带。RAG 对 Memory 是来源字符串定位。
- `_backfill_original_messages:69` 仅升级同作者原始 user 全文匹配记录；原 span 作为 legacy 数据保留；无法验证、派生或跨空间记录保持隔离。新 CHECK 依赖持久来源快照，不依赖 live message FK 必须非空。
- `0042:229` 的 downgrade 对非空 Memory/Candidate 表明确拒绝，不为回滚丢弃来源与确认历史。
- 本检查者没有独立执行带全部历史变体、重复记录、故意迁移失败和 FK 删除的迁移矩阵；实现者正在补该组回归。空库迁移成功不能代替该矩阵结果。

## 非阻塞建议与后续归属

- 新双线程测试初稿的 Barrier 在服务入口，仅证明两个线程同时开始，不保证二者均读到旧状态后再竞争。已建议实现者在首次 source resolve/access 完成后设置每线程一次的同步点，明确覆盖插入冲突/CAS loser 分支。此为测试稳健性建议，未发现并发生产实现的新错误。
- `index_memory` 现有直接重建仍会删除旧 chunk，文档/块幂等、算法版本活动指针和防复活属于已确认的 D 范围。A 的新增守卫能够拒绝已失效来源，不将 D 的旧问题记为 A 漏改；也不宣称保存引用跨重建稳定性已经解决。
- 中文召回、引用 attempt/build 认证与对历史/SSE重新授权归 B；Pi 历史/压缩归 C。本检查未重新扩大到这些模块。
- 建议主线程在任务 API 合同/执行记录中固化“legacy 恢复必须复核既存 scope，拒绝前不修改来源字段”，供 B/D 复用。未修改标记为历史的 `.trellis/spec` 文档。

## 验证记录

共享 `.venv` 是主检出的 editable install，不能用直接 `pytest` 的模块解析结果证明 worktree 行为。此检查先执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -c 'import app; print(app.__file__)'
```

实际输出为 A worktree 的 `backend/app/__init__.py`。所有 Python 探针保持此路径优先级。

| 检查 | 本检查者实际结果 |
|---|---|
| `ruff check --no-cache .` | 初次 fail，仅基线 0041 E501；主线程已修，最终统一复核待主线程汇总。 |
| `ruff format --check --no-cache .` | 初次 fail，memory_sources 与基线 0041；各自所有者已修，最终统一复核待主线程汇总。 |
| `PYTHONPATH=. .venv/bin/mypy app --cache-dir /private/tmp/familygraph-memory-rag/backend-check-mypy` | 初次 fail，4 个 API DTO 类型错误；实现者已修并已抽查，最终统一复核待主线程汇总。 |
| legacy 跨空间隔离探针 | 修复前 1 fail；修复后同探针 1 pass，真实迁移/生产 helper/index/search。 |
| 已通过的原 22 项/真实 listener smoke/新增 A 测试集 | 本检查者未重跑；分别由主线程/实现者负责，结果不得从本报告推算。 |

检查未联网、未调用真实模型、未访问主库。当前未发现其他 A 业务阻塞；不得在最终统一检查完成前把本报告解释为全任务验收完成。
