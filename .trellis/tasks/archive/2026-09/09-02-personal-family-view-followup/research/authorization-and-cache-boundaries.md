# PersonalFamilyView 后续任务研究：授权与缓存边界

## 授权边界

- PersonalFamilyView 按 `viewer_account + root_person + space` 隔离；客户端不能指定他人的 viewer/root。
- 家庭端点使用 `require_authenticated_user`，system_admin 不得通过家庭可见性链获得数据。
- 投影查询必须再次验证 active membership、明确 bridge 授权、VisibilityPolicy、view 状态和字段级 disclosure。
- `none` 节点/边/端点完全省略；masked 只能使用固定哨兵；lineage_summary 只能展示最小基线，不能作为遍历入口。

## 缓存边界

- ETag 只能用于同一授权上下文的安全快照复用，不能在授权检查前短路。
- ETag 输入至少包括 space_id、PersonalFamilyView view_version、policy/version 和合同版本。
- 撤权、Bridge revoke/expire、membership 变化、policy 收紧和空间删除必须使旧投影不可读；前端 epoch 只负责丢弃迟到响应，不能替代服务端授权复核。
- 空间统计只能返回授权聚合，不能以隐藏数量、relation_distribution 或错误码暴露未授权对象。
- 通知已读只改变 read_at；领域状态和 ActionCard revision 由各自领域命令维护。

## 风险

若仅依靠前端清缓存或 ETag，而没有查询层复核，撤权后仍可能返回旧数据；若复用旧全局统计，则会把不属于当前空间或当前 viewer 的对象计入结果。两者都必须由服务端测试锁定。
