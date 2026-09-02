# 系统管理员治理路由研究记录

## 已完成基线

- 独立 SystemAdmin 主体、独立 refresh session、JWT `principal_type`、`require_system_admin` 和安全元数据路由已经存在。
- 前端 system-admin 路由守卫、后台壳和治理概览已经存在；真正缺口是 `/system-admin/login` 占位页、PIN 首改后的错误回跳、主体感知 session expired 和 shell logout。
- 旧 `admin.py` 未注册是有意的安全边界，不是待简单补 include_router 的遗漏；该模块包含家庭数据 break-glass 能力。

## 权限结论

系统管理员只能处理账号/成员关系/空间/管理员归属/申请/交接工单的最小元数据投影。不得使用家庭 `User` 身份、`visibility.evaluate`、`is_admin`、`owner_id` 或其他空间管理员身份扩大权限。家庭 break-glass 必须另立任务，带独立主体引用、最小 schema、审批和审计。
