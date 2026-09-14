# D 修复执行前核对

2026-09-14，主线程在 B 串行修复期间只读核对累计基线 bd899b9。用户已授权修复、验收及通过后的提交、合并、归档和清理。本文件补充实现注意点，不改变原 20 组发现或历史探针。

## 来源与决定

- `backend/app/services/rag_maintenance.py:93` 的租约获取和 `:108` 的续租读取 ORM 缓存；`:219` 后游标与轮次普通赋值，印证 D-I03。实际写入必须以数据库条件更新和 rowcount 裁决，绑定不可变 attempt/owner/expiry/round/policy/target，不能仅刷新对象后再普通写入。
- `backend/app/services/maintenance.py:109` 调 RAG，`:116` 捕错后在 `:121` commit，印证 D-I04。RAG 批次应有完整回滚边界，独立于已有 core/assist 事务；最终栅栏失败后，索引、失败账本、游标三者都不留下部分结果。
- `backend/app/services/memory_sources.py:258` 的 source_lifecycle 先 flush，再检查传入 Memory 的当前属性；其 `_source_documents` 会重取根依赖，但 manual Memory 自身不能靠这个获得新鲜性。写锁内重取原 Memory/document 及配置后才决定物化，避免用先前加载的 active 状态通过撤销竞争。沿用该 resolver 的全局来源/读者权限区分。
- `backend/app/models/rag.py:60` 仍为普通 source 索引。canonical 唯一性须同时覆盖 index_memory 和 `memory_rag.py:780` 的 ingest_authorized_document，不能只修 maintenance。已有同源内容一致则稳定重放，不同 metadata/text/revision/status 则明确冲突；不能靠唯一键暴露未处理 IntegrityError。
- `backend/migrations/versions/0045_rag_index_lifecycle.py:83` 的降级先 DROP，再 DELETE 非活动块。新的前滚迁移不能单独保护这一旧降级入口：须把兼容性预检放在其第一项破坏动作之前，并去掉为满足旧唯一键而删除片段的行为。多版本无法满足旧键时应无损拒绝。

## 实现边界补充

1. B 本轮将新增 0046 ContextBuild 字段迁移；D 实施时重新读取真实 heads，再分配后续迁移。新增 document 唯一键/镜像检查时，SQLite 表重建必须保全 rag_chunks 的 ID、文本、版本和所有保存引用；FK ON/OFF 两态都验证，不能让父表重建触发 CASCADE。
2. `stage_index_version` 当前 `:275` 全量加载文档且忽略 worker。换版也遵守有限批量；目标必须有可执行的已知算法和当前持久策略授权，当前部署目标不是非法参数。普通 ensure 不能把已合法切换的指针改回模块常量。历史 v1 的固定切分与当前 v2 算法若需兼容，明确区分；合成 v3 只存在测试配置，不增加假生产能力。
3. 非 Memory 的授权导入当前没有独立原文保存供重新切分；`:291` 拼接旧重叠 chunks 既无顺序保证，也不能还原原文。无法验证原始材料的来源不自动换版，保留原活动版本并明确跳过/拒绝，不凭旧块拼出新“原文”。这不新增文档导入产品功能。
4. 完整性判断覆盖预期块的全部位置、文本、revision、version、状态与对应 FTS 行。补缺失块不能改写既有块，冲突/未知失效状态进入明确失败；“存在一块”不代表完整。FTS repair 复用来源生命周期，不以具体读者会员资格决定全局来源是否合法。
5. 每轮固定 upper watermark，游标只扫描该有限集合，后来高 ID 进入下一轮；失败记录依旧遵守退避。循环仅使用有限数据库事务，不接模型、网络或全库 admin 写接口。
6. 缺块恢复与同 revision 改文必须组合验证：原完整集合为 `[A,B]`，B 缺失后当前摘要变为 `[A,C]`，只核对现存 A 会错误补入 C。完整物化时应固定规范正文摘要或等价的完整集合证据；已有缺块且无可信摘要的旧投影不能用当前正文补签身份。`memory_sources.py:124,157` 的 `quote_sha256` 校验 `raw_quote`，不是 `Memory.content`，不能拿它替代索引摘要身份。允许新增有限 hash/完整性元数据，不新增原文存储能力。
7. FK ON/OFF 回归必须设置在实际 Alembic 执行连接上：`migrations/env.py:38` 自建 engine，不继承应用连接的 PRAGMA。`foreign_key_check` 为空不足以证明安全；同时比较 chunk ID/text/version、FTS 和保存引用，捕捉 CASCADE 删除但 JSON 依赖还在的情形。

## 必要回归形态

- 独立真实 Sessions：同源创建竞争；旧缓存遇到他人新 lease；锁前撤销来源。不能仅用同一对象两次调用或手工 attempt+5 代替。
- 真正 run_maintenance_tick 最终栅栏失败：assert 无新 doc/chunk/FTS/失败账本或游标副作用，并保留 core 的既定行为。
- 合法目标接管：切换后 search 能读新活动版本，随后 batch 不降级；旧保存依赖和 B 精确引用仍只指向原片段。
- 迁移矩阵：合法非空、重复 canonical、镜像冲突、legacy/unverified、tombstone、被保存 Memory 引用的旧版本块；在拒绝后比较原行/引用与第一项 DDL 是否发生。
- 保留原正对照：RAG-only 平台晚开启、未知 tombstone 阻断、读者暂时失权不全局失效、原文与确认记录保全。
- 失租异常不能在单条捕获中转成失败账本然后继续提交；普通坏来源可隔离，但整个批次的最终 lease/policy 拒绝必须回滚。
- 无法升级的非 Memory 旧投影仍能按其活动版本检索；不得因保守跳过换版而被进程版本常量排除。

以上第 6、7 条及最后两项由原 D 独立核验者在 2026-09-14 只读交接中提出，主线程沿源码抽查确认；未重跑历史探针。最终结果写入 D implementation/check 与复查 F-07～F-11；本文不将待执行回归标为通过。
