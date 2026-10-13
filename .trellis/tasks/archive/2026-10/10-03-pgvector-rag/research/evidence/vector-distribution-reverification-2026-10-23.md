# 真实 embedding 分布复测（2026-10-23）

## 为什么补这一轮

`10-03-pgvector-rag` 的「真实 embedding 分布（探针用确定性合成向量）」长期开着。
本轮在**隔离 PostgreSQL + 真实 embedding 服务**上重跑
`vector_similarity_distribution.py`，把该条闭合。

## 前置：golden set 必须先灌入并建索引

`vector_similarity_distribution.py` 要求库里已有分段向量。测试套件通过 `conftest`
fixture 在 SQLite 内存库里建，但那是**确定性 stub**，不是真实模型。

新增 `scripts/migration-proof/seed_golden_embeddings.py`：在隔离 PG 上复现同一份
语料（17 文档 / 20 chunk），并用真实 embedding 服务建向量索引。

```text
索引报告：IndexReport(examined=17, indexed=17, segments_written=17,
                     skipped_degraded=0, failed=0, batches=5, errors=[])
active 文档=17 chunk=20 分段向量=17
```

建 owner 时踩到一处真实契约：`claim_status` **不是 `User` 的字段**，而是
`Account.status`；直传 `User(claim_status=...)` 会 `TypeError`。已按
`tests/conftest.py::create_user_with_pin` 的同构写法修正。

## 实测结果（隔离 PG + bge-small-zh-v1.5，512 维）

```text
case                       mode             best_relevant  best_irrelevant  gap
update-residence           answerable             0.5726           0.5243   0.0483
update-occupation          answerable             0.6137           0.5712   0.0425
update-preference          answerable             0.7207           0.4400   0.2807
extract-birthday           answerable             0.6580           0.4804   0.1776
extract-dietary            answerable             0.6266           0.5336   0.0930
extract-term               answerable             0.6782           0.5148   0.1635
temporal-history           answerable             0.7336           0.5998   0.1337
temporal-migration         answerable             0.7716           0.6236   0.1480
multi-session-school       answerable             0.5197           0.3859   0.1338
multi-session-festival     answerable             0.5091           0.4492   0.0599
multi-session-story        answerable             0.7356           0.5000   0.2356
abstain-unknown-person     abstention                  -           0.4859        -
abstain-unknown-topic      abstention                  -           0.3811        -
abstain-unknown-trip       abstention                  -           0.3634        -
expired-not-current        answerable                  -           0.7712        -

相关 top-1:      min=0.5091  p50=0.6580  max=0.7716
不相关 top-1:    min=0.3634  p50=0.5000  max=0.7712
弃答虚假 top-1:  min=0.3634  p50=0.3811  max=0.4859

OK  存在可用间隙：弃答虚假最高 0.4859 < 相关最低 0.5091（地板 0.50 落在其中）
PASS: 分布重叠（余弦相似度不可作判据）且存在可用地板间隙
```

## 结论（与 2026-10-10 完全一致）

数值与三个月前在真实模型上得出的结论**逐位一致**，因此：

1. **余弦相似度不能作相关性判据**：相关与不相关分布**重叠**——不相关最高 0.7712
   **高于**相关最低 0.5091。任何用余弦阈值做相关性判定或重排的方案都是错的。
2. **`_VECTOR_MIN_SIMILARITY = 0.50` 的取值依据成立**：它落在弃答虚假最高 0.4859 与
   相关最低 0.5091 之间的 0.023 宽间隙内，只做一件事——丢掉明显无关的向量补充，
   让「库里没有」仍表现为空结果。
3. **换模型必须重跑本探针**：0.50 是**这个模型在这个语料上**的值。

## 与索引基准探针的分工（不要混淆）

- `pgvector_index_comparison_probe.py` 用**有簇合成向量**，量化**索引结构**的
  recall 与延迟（10 万行 / 512 维 / HNSW build 120.5s）；
- 本探针用**真实模型**，量化**相似度尺度**，决定地板取值。

两者回答不同问题，都用合成数据互相不能替代：前者要规模，后者要语义。

## 环境清理

```text
fg_probe_emb：已 DROP（pg_database 中无 %probe% 残留）
线上：未接触
开发服务（embedding 8091）只被查询，未被修改
```
