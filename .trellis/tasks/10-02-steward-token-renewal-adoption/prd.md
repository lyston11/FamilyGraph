# token 续签未被所有请求路径采用：长 run 在 600s 后 events/append 与 provider 仍 401

## Goal

b656d89 只把续签 token 写回 job，但 startEventFlusher/buildRunSession/executeTool 已按值捕获旧 token；run 466/467 存活 601/602s 后 events/append 与 provider/responses 401 而心跳 200

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
