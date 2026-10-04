# S5 移除 in-process 辅助路径与开关

## Goal

删除 _post_json/_API_PATHS 与 VIA_PI 开关，全量检查与 Compose 联调

## Requirements

- TBD

## Acceptance Criteria

- [ ] TBD

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.

## Resolution (2026-10-04): superseded, no work remains

本任务的目标已由 `09-28-steward-pi-cutover` 完成并归档（S5：删除 in-process 辅助
路径与按 kind 选择载体的开关）。核对当前代码后确认：

- `_post_json`：只剩 `__pycache__` 中的旧字节码，源码中已无。
- `VIA_PI`：已无任何引用。
- `_API_PATHS`：**仍然存在**，但它不是 in-process 残留——`provider_proxy.py` 用它做
  Provider 协议 fence（判断请求打的是 `/chat/completions` 还是 `/responses`）。
  S5 当时的结论就是「不能随删除旧载体一并机械移除」，这里保持该结论。

因此本任务没有剩余工作，按「已由 09-28-steward-pi-cutover 交付」归档；不再作为
待办保留，避免以后误判仍有 S5 清理要做。
