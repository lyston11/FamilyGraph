# m2a 技术设计

> 契约：architecture.md §6 授权矩阵（QU1=B + AD-9）。M2/M3 所有数据出口的前置门禁。

## visibility.py 单点实现

```python
LEVEL_FULL/SUMMARY/INVISIBLE
MASKED = {"__masked__": True}

classify(session, viewer_id, target_id) -> 'full'|'summary'|'invisible'
  full    ⇔ target==viewer ∨ shared_active_space ∨ direct structural active edge (elder/younger/spouse)
  summary ⇔ clan 可达（沿 active 边无向 BFS）
  else invisible

user_payload_for(session, viewer, target) -> dict | None
  invisible → None（路由转 404 防枚举）
  full      → 完整档案字段
  summary   → {id,name} + AD-9 披露开关已开放类别的真实值；
              未开放类别 → MASKED 结构（birth/death/bio/avatar_path）
graph 过滤：clan scope 节点逐个 classify；invisible 不返回；summary 节点仅基线+披露字段
```

- custody.py 保留 edit/delete 判定（D5），visibility 只管 view 层级——两者在 GET/PATCH 入口并存。
- 矩阵其余行落位：头像/附件下载端点（m3a 实装时消费）、统计聚合范围与搜索命中（m3c/m3d 消费）。
