# m2b 技术设计

> 消费 m2a 可见性；clan scope 图接口已就绪。

- FamilySpaceView 增加「家庭 ⇄ 家族」切换：family=空间子图（space_id 过滤），clan=连通分量
- clan 视图节点两态渲染：full 卡（同空间/直系）与摘要卡（MaskedField 锁样式 + 「申请进入」按钮 → POST /api/spaces/{owner_space}/join）
- 折叠：v1 以 depth 参数控制（默认 5 全量，几十人规模）；分支折叠 UI 归 M4 打磨
- 切换动画：CSS transform 缩放过渡（基础版）
