# 技术设计

## 后端

`rebuild_space_views` 将“需要重建”定义为存储状态非 current，或任一物化版本字段与当前 `POLICY_VERSION`/`COMPUTATION_VERSION` 不一致，或缺少输入指纹。这样周期 integrity scan 能直接发现重启后的旧视图，并沿用现有逐视图 SAVEPOINT 重建与 fail-closed GET。GET 对版本漂移仍只返回安全空 payload；`request_view_recompute` 保持非阻塞语义，但改用不会被 succeeded 水位吞掉的扫描原因（`integrity_scan`）或等价路径，确保启用 steward 时能产生工作。

## 前端

`spaces.loadMembers` 在请求开始时保留现有 members/transfers/profileRefs，仅在响应仍属于当前 generation 和目标空间时整体替换；新增可观察的加载错误状态，成员失败时保留旧授权投影并让调用方看到失败（路由守卫仍拒绝放行）。守卫只临时读取目标空间成员，不改变 `currentSpaceId`，因此 `loadMembers` 增加可选 `setCurrentSpace`（默认 true）或独立的 `refreshMembers` 入口。空间切换使用 refresh 入口并将错误向上传递/记录，避免静默伪装成无权限。

## 边界

仅修改 PFV 服务及其回归测试、spaces store/useSpaceContext/router 及相关测试；不改变 API schema、ETag/304、授权 fail-closed 规则或其他投影缓存策略。
