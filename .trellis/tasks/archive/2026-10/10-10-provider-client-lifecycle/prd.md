# Provider 连接生命周期基准与持久 client 决策

## Goal

量化每请求新建 AsyncClient 的代价与连接池复用风险，决定是否改为持久 client；同时修正 10-04-provider-reliability-boundaries 过时的覆盖证据

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
