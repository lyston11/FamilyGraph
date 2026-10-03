# Steward assist 预算估算与输出校验：space 2 持续 insufficient_budget，ranking 持续 invalid_output

## Goal

两个仍在发生的辅助质量问题：(1) _estimate_input_tokens 按 1 token/byte 估算，space 2 的 6564 字符 prompt 使 job 预算（20000 tokens / 6 calls）装不下后续 attempt，落 skipped/insufficient_budget 42 次；(2) invalid_output 15 次（ranking 11），模型输出未通过封闭校验、增强被丢弃回落确定性产物

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
