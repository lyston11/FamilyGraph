# H 方案执行计划

当前只规划。本任务下一阶段是研究与方案评审，不是直接实现delta。

- [ ] 按已有批准范围推进；如需激活任务通过task.py，不因父in_progress自动开始代码。
- [ ] 读后端事件schema/注册表、agent events/policy hooks、frontend agent store/SSE/citation组件，核对全部消费者。
- [ ] 引用D/F计时与真实传输证据，量化可减少的发布等待，列首token尚慢的上限。
- [ ] 冻结候选消息身份/seq/attempt/epoch/重放/终态替换字段表，绘制正常与失败时序。
- [ ] 审查跨chunk输出安全、引用延后绑定、撤权与重连；不能证明等价安全则推荐保留完整消息或延期。
- [ ] 提交临时内容保留、滚动、读屏、频率与缓存上限的交互建议及待选项。
- [ ] 写后端→sidecar→frontend兼容发布/回退表和后续测试矩阵；检查旧客户端继续得到完整答案。
- [ ] 输出采用/延期/不采用评审结果；方案AC全部满足且决策已记录才可完成方案任务。

## 如果用户后续选择实现

先更新PRD新增实现AC并重新评审，再start/进入本任务worktree；不以本计划的条件分支作为实现授权。实现必须经过后端schema→sidecar映射→frontend投影顺序，并保留一次完整权威消息、空答案失败、取消失租与完整历史合同。

后续验证入口：backend `tests/test_agent_events.py`、`tests/test_provider_proxy.py`；agent `test/events.test.ts`、`test/worker.integration.test.ts`、`test/assistant-delta-gap.test.ts`、`test/session-history.test.ts`；frontend agent store/SSE API/组件既有测试。运行受影响包完整门禁，真实internal联调、API smoke及F式浏览器生命周期检查；G式发布和小样本验证另行确认范围。

## 交付与清理

产物为协议字段表、状态/错误矩阵、交互决策、兼容/回退、验收计划与限制。仅文档检查不宣称代码/浏览器通过。更新父任务可选项状态，不将“设计完成”写作“增量已上线”。使用task分支时提交后串行集成、归档并清理满足条件的worktree/分支。
