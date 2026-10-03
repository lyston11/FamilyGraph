# 实施计划：pgvector 优先的 RAG 检索扩展

## Phase A：PostgreSQL 依赖确认

- [ ] 等 PostgreSQL schema/事务实验确认后建立 embedding/index 原型表和约束。
- [ ] 设计 source/document/chunk/revision/scope/visibility/citation 外键与索引。
- [ ] 明确 embedding model/version、content hash、index version 和删除/撤权状态。

## Phase B：pgvector 基准

- [ ] 建立合成且不含真实个人数据的规模数据集，测试 exact/ANN、过滤、写入、更新、删除、重启和备份恢复。
- [ ] 测量查询 p50/p95/p99、更新延迟、索引构建、空间隔离和 background budget 对 control-plane 的影响。
- [ ] 验证授权 projection 先于向量查询，结果只能产生合法 citation/handle。

## Phase C：生命周期与故障

- [ ] 实现 revision 切换、撤权不可见、删除回收、失败重试、旧版本保留和原子 active pointer。
- [ ] 注入 PostgreSQL/worker/embedding provider/Redis（如使用）故障、重复消息和半批失败。
- [ ] 运行现有 memory/rag/index/citation/visibility 回归与 mutation。

## Phase D：选型结论

- [ ] 将 pgvector 与独立向量服务按规模、延迟、过滤、授权同步、运维、备份和故障回退比较。
- [ ] 若 pgvector 达标，保留 pgvector-first，不创建独立服务；若不达标，另建独立 vector DB 子任务，不直接生产接入。
- [ ] 记录 RAG 关闭、来源失效、revision 冲突和权限撤销的用户可见行为。

## 回滚

关闭向量查询并回退确定性/既有词法路径；保留 source/revision/chunk 数据和旧 active version，不删除合法引用。索引构建失败不得切换 active pointer。
