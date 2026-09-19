# 家族空间准入边界：自行申请须有亲属关系且由空间管理员审批

## Goal

修复可自行加入他人家族空间的越权入口：join-by-user 改为需与空间内成员存在 confirmed 亲属路径、目标空间按 owner/space_admin 解析、pending 只能由该空间 space_admin 审批；同时让家族树路径解析不再被非本空间成员的中间人切断。

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
