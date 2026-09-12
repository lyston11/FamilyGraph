# Implement — 管理员壳层布局几何修复

1. 在 `.admin-shell` 明确声明横向 flex 主轴，并移除/收敛会覆盖壳层的旧布局规则。
2. 对齐 `.admin-sidebar`、`.admin-shell-body`、`.admin-header`、`.admin-main` 的桌面和移动断点，保证内容首屏连续。
3. 保留导航和账号行为；仅在需要时补充结构 class 或 aria 细节。
4. 新增回归测试，验证受保护路由挂载壳层并确保壳层结构含侧栏与 body。
5. 运行管理员 type-check、lint、tests、build、task validate 与 `git diff --check`。

风险点：样式文件存在历史重复规则；修改时优先删除冲突声明或在壳层区块集中覆盖，避免继续堆叠同名选择器。
