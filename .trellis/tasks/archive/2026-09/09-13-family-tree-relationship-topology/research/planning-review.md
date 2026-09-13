# 独立规划复核

日期：2026-09-13。范围：PRD、后端拓扑/授权/ETag、前端结构/布局/回退、个人路径入口和推测层串行兼容。只读 explorer 审查，主线程负责取舍和原文复核。

## P1：桥接自然到期的授权前提

- 证据：backend/app/services/relationship_graph.py:170 的 active_bridges 只限制 status；backend/app/models/personal_family_view.py:169 已有 expires_at。
- PFV 复用 bridge_user_ids 授权显示（personal_family_view.py:450）；单纯时间到期不会改变当前查询结果和图指纹，最终载荷哈希也不能自行修复授权。
- 现有 test_personal_family_view_consistency.py:693 手工修改 status=expired，未覆盖无事件、仅时间到期。
- 主线程已直接阅读上述代码并确认。设计已补充共享图读取入口的 active + 未到期判定、统一有效集合与只读行为；PRD AC6 和实施测试矩阵已明确时钟到期验收。
- 处理状态：**已在规划中解决，代码修复和测试尚未执行**。后续 check 必须验证实际实现，不能把本记录当测试通过。
- 聚焦复核已确认原 P1 的设计补足；另指出独立授权节点“保留”与旧投影安全空态存在 P2 表述歧义。已澄清为授权资格保留，首次 GET 返回失效安全空态，重算后重新显示；测试分两个时点检查。

## 其他结论与限制

- 未发现其他规划阻断：直接事实查询覆盖非主路径关系；世代/夫妻块/冲突回退自洽；个人路径仍可由资料页访问；旧响应缺字段和合法空数组有区分；推测与 confirmed 结构分离。
- 本审查没有运行产品测试或修改业务代码。截图、性能与真实 API 结果须在实施阶段验证。
- 推测层另一 worktree 仍有 WIP，实施前重新核查最新基线并串行处理共享文件。

## 规划包检查

- PRD 已完成收敛重写并从头阅读，R1–R6 与 AC1–AC8 映射保留，无占位需求或未解决产品选择。
- task.py validate 通过：implement.jsonl 1 条、check.jsonl 2 条有效上下文；文件存在且可读取。
- 本任务文档无尾随空白和 TBD/FIXME 占位。任务仍为 planning，未建立代码 worktree、未运行产品测试。
