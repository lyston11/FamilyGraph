# 验证摘要：候选 prompt 契约与规模降级

日期：2026-09-19 · 分支 `feat/09-19-steward-candidate-prompt-contract`

## 结论

8 项 AC 全部通过；核查发现 1 个中等缺陷（注释与边界语义不符）已修复并加回归守护。无阻塞项。

## 关键结果

| 项 | 结果 |
| --- | --- |
| 定向回归（14 个文件） | 212 passed |
| 完整后端套件 | 1728 passed, 3 skipped |
| ruff check / format / mypy | 全绿（406 files formatted, 207 sources typed） |
| 评测门槛 | hard_gate 100%、负例全空、召回 1.0、安全用例 27 |
| prompt_version | `dbf4c72e…`（main 为 `edb657bd…`，确认已变更） |
| 候选 prompt | 582 → 1214 字节，含方向 / 矛盾禁止 / 可解析最小示例三条款 |

## 修复的缺陷

**D1（中等）**：`candidate_user_content` 的 `low == 0` 分支注释误写「连空投影都装不下」；实测在
`cap == system + 空投影` 这一端点值时空投影恰好装得下，能装下的子集是 0 条。**行为正确**
（拒发空输入、如实回落 `prompt_too_large` 而非伪装成功），仅注释错误，已更正并新增
逐 cap 扫描与四条优先级/良性语义回归。

## 已核实的核心前提

- 投影字节随事实数单调不减 → 二分查找取到的是**最大**可行子集。
- 预算 `overhead = len(system) + 1` 与实际发送的 `f"{system}\n{user}"` 精确一致。
- 子集化不改变 `evidence_hash`（仍覆盖全量事实）、不计费、不重放、无网络调用。
- 终态优先级 `unknown > failed > prompt_too_large`；预算类 `skipped` 保持 `applied`。

## 未做

真实模型质量评测、生产库只读核对、生产部署（S7 需单独授权）、前端门禁（未改前端代码）。
**不声称候选质量已改善。** 详见 `evidence/validation.md` 与 `check-report.md`。
