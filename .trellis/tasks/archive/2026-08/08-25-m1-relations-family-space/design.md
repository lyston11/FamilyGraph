# M1 技术设计（父级）

权威契约：architecture.md §1（身份模型）/§3（空间生成）/§4（FSM）/§5（DB 契约与布局规则）/§7（删除级联）。里程碑级决策：

- **users 模型演进策略**：m0b 建 users/accounts 骨架 → m1a Alembic 增量加档案字段。禁止重写初始迁移。
- **合并请求是 M1 的中枢契约**：connection_request 同时驱动 relations 与 space_members 两条 FSM，由 services 层单一函数保证原子性，m2c 的审批 UI 只是消费该接口。
- **图查询先行**：m1b 的 `GET /graph/me` 是三布局的唯一数据源，可见性参数位预留（m2a 接入），避免布局层二次改造。
- **布局失败回退**是一等行为而非异常处理：树状布局器返回 {ok:false} 时前端切画布模式并提示，数据修复前不阻塞使用。
