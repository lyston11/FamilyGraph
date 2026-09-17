# 实施与验收

- [x] 启动隔离 worktree，读取相关 Spec；实现管理员会话恢复修复和针对竞态的回归。
- [x] 定向回归先红后绿，管理员 lint/type-check/test/build，独立审查。
- [x] 浏览器验证成功恢复留原页、失败回登录、首改密门禁；不暴露凭据。
- [x] 只读核对生产配置、保存 env 安全备份，仅启用 terminology env 键，重启 API。
- [x] 检查健康、有效平台/空间/Provider/许可及后台作业、真实调用状态；如实记录模型结果。
- [x] 更新 Spec/HANDOFF/验收记录，提交集成推送。
- [x] validate、archive、清理 worktree/分支，检查主检出干净。

验证入口：`cd system-admin-frontend && npm run lint && npm run type-check && npm test && npm run build`。后端无源码修改则不重复全量后端测试，治理和生产只读探针验证环境变更。敏感 env 仅服务器 0600 备份；任务证据仅含状态不含密钥、token、模型 prompt。


## 执行记录（2026-09-17）

- 修复提交 `a5094ba`，合入 `66edd59`；两项本次引入的健壮性回归（首次导航 reject 未挂载、storage 不可用卡住 restoring）已修并补回归。
- 门禁：manager-admin lint/type-check/test(127)/build 全绿；红基线 7 failed → 绿 33 passed；真实浏览器三场景通过。
- 生产启用：env 备份 0600 + 原子追加 `STEWARD_ASSIST_TERMINOLOGY=1` + 重启 API；生效值空间 2 为 True，空间 1/3 保持 False。
- 真实调用结果见 `research/model-acceptance-2026-09-17.md`：启用生效，2 笔真实发送，无合法输出，无自动写回，`unknown` 不重放（遗留阻塞已记录）。
