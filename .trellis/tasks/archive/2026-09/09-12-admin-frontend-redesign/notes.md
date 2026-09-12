# Notes

- 2026-09-12：根据用户端与管理员端截图，确认管理员后台存在明显的产品视觉割裂：导航和概览缺少品牌层级，指标与表格过于普通。
- 2026-09-12：完成 FamilyGraph Observatory 视觉重设计。共享壳层加入品牌锁定、后台上下文状态、编号导航、统一焦点环；概览加入 editorial 标题区、快照状态提示、强化指标卡和表格层级。
- 保持管理员 API、路由、认证、权限边界、既有 class/testid 和数据绑定不变。
- 验证：`npm run type-check`、`npm run lint`、`npm test -- --run`（83 passed）、`npm run build` 均通过。现有测试有既存 Vue PageState/jsdom 网络警告，但无失败。
