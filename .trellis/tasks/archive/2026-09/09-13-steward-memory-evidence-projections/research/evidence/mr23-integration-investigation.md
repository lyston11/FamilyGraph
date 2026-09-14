# MR-23 发布交付与迁移调查

## 方法与范围

只读核验已集成基线 `dee91a1` 的 `backend/tests/test_steward*.py`、相关迁移测试、delivery/pipeline 和 0048 迁移。未运行模型或数据库迁移；相对路线另通过无数据库 Alembic 内存图解析验证。行号指该基线，符号名用于后续定位。

## 真实链路入口

| 职责 | 源位置与符号 |
| --- | --- |
| 真实迁移库 | `backend/tests/conftest.py:48`，session Alembic fixture |
| 人物/空间/成员 | 同文件 `create_user_with_pin:233`、`create_agent_fixture:370`、`create_space_member:433` |
| 确认真源事实 | `backend/tests/test_steward.py:98`，`_confirm` |
| lease/enqueue/execute | `backend/tests/test_steward_staged_pipeline.py:98`，`_run(deliver=False)` |
| publication 指针断言 | `backend/tests/test_steward_terminology_delivery_integration.py:128` |
| 实际交付 | 同文件 `_drain:71`，真实 `steward_delivery.drain(generation_id=..., limit=128)` |
| fake transport 与 batch | `backend/tests/test_steward_assist.py`：`_provider:47`、`_steward_setting:63`、`_turn_on:90`、`_responses_fake:96`、`_run_assists:170` |
| 私人驳回 | `backend/tests/test_steward_suggestions.py:212`，真实 `dismiss_suggestion` 和 CAS |
| 共享与私人驳回并存 | `backend/tests/test_steward_suggestion_quality.py:547`，真实 `dismiss_edge`；不要复制 `_same_origin:519` 的手造主链 |

`register_batch_for_job:584` 要求非空 `facts_brief`。`_space_flag_on` 会另插 setting，不能与 `_steward_setting` 合用。导入 `_run` 不继承其模块 autouse fixture。drain 不处理 inferred_overlay，不能以所有 intent 都 done 作为候选交付成功条件。

## 捕获版本与执行 fence

`steward_pipeline._prepare_delivery:496` 调用 `steward_delivery.prepare_intents:222`，当前 candidate payload 位于 `:324`。在真实 core 返回后检查固定 candidate/version ID，再于来源输入不变时记录 V2，旧代重复交付只终结 V1。下一 core 才捕获 V2；两个 pending 版本必须具有不同 intent_key，唯一性为 `(generation_id,intent_key)`。

改变 SourceFact 的时间必须与该用例分开。若准备后才确认 Q，旧 generation 先被输入 fence 拦住，不能证明精确捕获版本 ID。

`test_steward_delivery_recovery.py:137` 提供原 `_claim_due`、独立 Session 改租约及旧 owner 拒绝覆盖的切点；真正应用前由 `steward_delivery._matches_claim:795` 重验。应修改 DeliveryIntent 的 lease/owner/attempt。过期 generation 或真实 revision/授权变化分别验证 `drain:875、893` 的第二次 fence；仅发布较新 generation 不必然使旧代失效。

## 迁移预检

`test_steward_terminology_publication_migration.py:20` 固定 HEAD=0048，断言在 `:131、150`，新迁移需要将当前头动态化；PARENTS 和源数据/索引/trigger 断言保持。

`test_rag_lifecycle_migrations.py:299、321` 与 `test_memory_source_migration.py:214` 要求拒绝深降级时 DDL=0，完整 schema/head 不变。新迁移不能先 drop 空版本表，随后才由父迁移拒绝。

`0048_steward_terminology_publication._preflight_parent_downgrade:174` 已有目的计划感知的拒绝 SQL，并在 `:194` 通过零行 UPDATE 建立 writer。增加可选 planned 参数能复用该逻辑，默认分支不变；0049 先从自身 revision 解析实际计划，再传入并检查自有证据。

无数据库图解析结果：0049→`-1` 计划仅 `[0049]`；错误地从0048解析`-1`会出现 Ambiguous walk；0049→`-2` 本身也因0048 merge歧义。0049→绝对0047的计划为`[0049,0048]`，之后0048无参重算绝对目的地仍一致。因此当前图无需计划缓存；应覆盖真实`-1`成功、`-2`无DDL拒绝、绝对深降级保全。

## 否定结论的检索范围

在上述测试文件范围内，没有现成 pytest 同时串起 fake candidate、分阶段发布交付、私人和共享驳回，也没有 evidence-version-ID 捕获用例。归档旧 harness 的 ROOT 相对层级与新增公开建议断言均不适用，不能原样运行或覆盖其历史输出。
