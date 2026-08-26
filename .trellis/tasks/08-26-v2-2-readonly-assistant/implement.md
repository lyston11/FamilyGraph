# V2.2 只读 Assistant 实施计划

- [ ] 实现 AgentQueryService 与六个版本化只读工具，限制分页/路径长度/输出大小。
- [ ] 编写 Assistant 系统提示词，明确事实状态、路径引用、拒绝越权和资料不足行为。
- [ ] 实现 Session/Message/Run 用户 API 与端到端只读权限测试。
- [ ] 新增 agent API/store/SSE composable；扩展 `auth.clearSession()` 与空间切换清理。
- [ ] 在 App.vue 挂全局 Launcher，交付桌面 drawer/移动全屏、Session 列表、消息流、取消/重试。
- [ ] 加入工具摘要与证据路径展示，不暴露原始内部 payload。
- [ ] 完成 a11y、响应式、断线/刷新/401/撤权/跨 scope 对抗测试。

## 验证

```bash
cd backend && pytest
cd backend && mypy app
cd agent && npm run type-check && npm run lint && npm test && npm run build
cd frontend && npm run type-check && npm run lint && npm test && npm run build
docker compose up --build
```

E2E fixture 至少包含两个用户、两个空间、household/lineage/provisional/minor/masked 节点；对每个问题比较 API 可见投影与 Assistant 答案。

## 回滚

Assistant UI 和工具注册由 feature flag 控制；回滚时保留 Runtime 表/事件，不删除用户已有 Session，关闭入口即可。
