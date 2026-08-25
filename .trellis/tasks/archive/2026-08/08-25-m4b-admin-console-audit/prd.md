# m4b 管理员后台与审计

> 父任务：[08-25-m4-polish-admin-deploy](../08-25-m4-polish-admin-deploy/prd.md)｜依赖：m0b（audit_log 已有基础）

## Goal

极简管理员后台：A4 三职责 + 审计可视化。

## Requirements

- 入口仅 is_admin 可见（路由守卫+API 双重校验）。
- 三功能：①用户搜索→重置 PIN（新随机 PIN 一次性展示，token_version 失效旧会话）②数据修正：改名/改归属模式/转移代管权（处理创建者失联）③删除档案特权通道（二次确认+级联，复用 m1a 服务层）。
- admin_audit 页面：action/target/ip/时间列表，只读；日志字段与保留策略见 spec/backend/logging-guidelines。

## Acceptance Criteria

- [ ] 重置某用户 PIN 后其新 PIN 可登录、全部旧会话即刻失效。
- [ ] 代管权转移后原创建者写接口 403、新代管者可编辑。
- [ ] 非管理员访问 /admin 路由与 API 均 403；全部操作在审计页可见且不可篡改入口。

## Non-goals

- 批量管理/角色细分；数据看板。
