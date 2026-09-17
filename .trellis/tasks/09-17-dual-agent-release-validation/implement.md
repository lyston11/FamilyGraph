# G 执行计划

仅规划；本文件不是部署或真实调用记录。主会话串行，无子智能体。

## 准入与准备

- [ ] C/D/E集成、F必需验收通过；复核最新PRD/design与父AC状态。获得针对最新计划的执行批准后才start本任务。
- [ ] 只读核对实际运行版本、进程、迁移、队列和非敏感配置；不因先前快照重复部署。
- [ ] 制作候选manifest、旧制品/配置回退清单、兼容顺序与维护窗口；把可审阅结果准备好再确认尚未授权的生产动作。
- [ ] 核对真实样本6逻辑/12物理请求与token/时间提案是否适用，确认金额上限；现有授权覆盖的项目不重复询问。
- [ ] 生产online backup，隔离副本迁移与恢复演练；DATA_DIR必须在app导入前指定。

## 发布与验证

- [ ] 检查在途任务与租约，按批准窗口排空/停止，避免旧worker补写；不清理unknown绕过去重。
- [ ] 同步冻结代码、安装锁定依赖/构建agent dist、迁移（若有）并按兼容顺序重启user services。
- [ ] 核对PID/启动时间/制品hash/实际OpenAPI与业务行为；backend及sidecar健康、内部认证与租赁都要验证。
- [ ] 运行目标环境的API smoke与真实浏览器流程；exit2=blocked。相关家庭/admin listener互拒仍通过。
- [ ] 在隔离合成环境先一笔真实调用验证整链，再按已批准总预算补小样本；记录每笔物理请求、取消/截止与费用来源。
- [ ] 分别判定可达/已调用/合法输出/已保存/应用或拒绝；真实样本与fake矩阵分开，配置不同不计算提速百分比。
- [ ] 核对生产业务计数未被测试污染，服务健康与队列推进；有活跃隔离worker时保留DATA_DIR，不提前删除。

## 收尾

- [ ] 更新父验收AC01～09与本任务证据、HANDOFF；任何必需项blocked保持父未完成。
- [ ] 提交运维证据（无敏感值）；串行集成/归档。执行集成的主会话检查分支已合并与worktree干净后立即remove及branch -d，不使用force。
- [ ] 报告本任务及父任务的清理结果；旧父worktree如果仍有未合并/未提交内容，保留并说明，不静默删除。

## 命令入口（不是直接执行清单）

- 生命周期命令在主检出；代码/脚本修改在task.json登记的worktree。
- 远端核查：`systemctl --user show familygraph-api.service familygraph-agent.service`；具体启动参数/cwd以实际unit为准。
- 数据备份使用`python -m app.backup`或既有Compose入口，禁止直接cp运行中SQLite主库。
- 迁移先在隔离DATA_DIR运行`alembic upgrade head`；部署时再次核对单head。
- 根目录`./scripts/frontend-api-smoke.sh --report /tmp/familygraph-release-smoke.json`；先查脚本配置，确认目标listener与认证域。

失败处理：先停止新调用并保全审计，再按已批准manifest回退。报告错误码/状态，不输出env、凭据、SQL参数或模型正文。
