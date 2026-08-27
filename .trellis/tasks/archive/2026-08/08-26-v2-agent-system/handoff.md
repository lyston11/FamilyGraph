# FamilyGraph v2 Agent 规划交接摘要

## 当前状态

- 新父任务：`08-26-v2-agent-system`，状态 `planning`。
- 七个子任务均已创建并保持 `planning`；未运行任何 `task.py start`，未修改产品代码。
- 需求压力测试已收敛，Blocking Open Questions = 0。
- 当前系统尚未部署，无真实用户或数据；不需要生产数据迁移与兼容分支。

## 一句话架构

Browser 只访问 FastAPI；FastAPI 持久化 Agent 状态并执行全部领域授权；Node Pi sidecar 只运行模型循环和受控工具编排；Assistant 绑定 user+space，Steward 绑定 space+job，二者都不能直接访问业务数据库或越过 VisibilityPolicy。

## 不可破坏的不变量

1. SourceFact 必须经相关人员确认，Agent 无直接写路径。
2. Session、Memory、RAG、工具与缓存均为单空间 scope。
3. Steward 不继承系统管理员身份，只读当前空间授权投影。
4. 空间不自动合并；配偶/亲属关系只产生桥接或申请卡片。
5. provisional 人物未确档前不进入推荐池。
6. 用户原始称谓不被词典或 Agent 覆盖。
7. Pi Guard 是前置防线，FastAPI 仍要最终鉴权。
8. SSE 重连只重放事件，不重新执行副作用。
9. 敏感内容强制本地 Provider；不可用时拒绝。
10. 受控联网默认关闭并最后交付。

## 下一步

用户审阅本轮完整规划后，如明确批准进入实现，应只启动 `08-26-v2-0-foundation`：

```bash
python3 ./.trellis/scripts/task.py start 08-26-v2-0-foundation
```

不要启动父任务，也不要跳过 Foundation 先接 Pi。

## 任务地图

`V2.0 Foundation → V2.1 Runtime → V2.2 Assistant → V2.3 Relationship Intelligence → V2.4 Steward/ActionCard → V2.5 Memory/RAG/Guard → V2.6 Controlled Web`。

详细需求见父 `prd.md`，总体边界见父 `design.md`，跨阶段门禁见父 `implement.md`，决策编号见父 `notes.md`。
