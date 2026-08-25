# M2 技术设计（父级）

权威契约：architecture.md §4（请求合并语义）/§6（授权矩阵）。里程碑级决策：

- **visibility.py 是全项目安全核心**：签名固定为 `apply_visibility(viewer, resource_dict, relation_hint) -> dict`，所有出口强制过此函数；m3a/m3c/m3d 新增出口时复用同一函数而非另写判定。
- **连通分量计算**：clan scope 在图查询时以 BFS 从用户 active 边扩展，几十人规模无需预计算表；结果不缓存（权限实时性优先）。
- **遮罩结构契约**：`{__masked__: true}` 判别联合是前后端共同契约，types/api.ts 与 Pydantic 同步定义，前端 MaskedField 唯一渲染点。
