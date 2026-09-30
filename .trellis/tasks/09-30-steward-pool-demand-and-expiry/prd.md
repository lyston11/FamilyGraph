# 连接需求超过池上限，且 run 244 修复后仍 expired 原因未定

## Goal

事件循环阻塞已修；剩余两个独立问题：(1) steward core 4 线程 + assist 2 + 心跳/lease 轮询共同争抢 15 条连接，实测慢响应时 4 线程等连接仅 2 在执行；(2) run 244 心跳已稳定 20s 但仍 expired，原因未定位

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
