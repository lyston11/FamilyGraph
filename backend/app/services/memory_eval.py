"""记忆/RAG 检索质量评估（P0 基线）。

## 为什么需要这个模块

本任务的全部改动都是**质量改动**（取代语义、提取面、重排、分层预算）。没有基线就
无法判断一次改动是提升还是回归，也无法回答「是否值得引入 cross-encoder 或向量路径」。
这是 `10-05-migration-proof-gates` 建立的证明门在检索领域的对应物。

## 指标与它们各自的诚实边界

```text
retrieval recall@k   命中的期望来源 / 全部期望来源   —— 只衡量「找没找到」
citation 正确率      k 条命中里属于期望来源的比例    —— 衡量「有没有拿噪声凑数」
forbidden 命中数     不该出现的来源被检索到的次数    —— 取代语义的直接证伪指标
弃答正确率           期望为空时确实返回空的比例      —— 「不知道就说不知道」
延迟 p50/p95         每次检索的墙钟时间              —— 成本侧
```

**这不是回答质量**。回答准确率需要真实模型（provider egress），本模块刻意不调用
任何模型：它只评测**检索层**，因为检索是回答的必要条件，且可完全离线、可复现。

## 与 fixture 的关系

fixture（`tests/fixtures/memory_eval/golden_v1.json`）表达**期望行为**，不迁就实现。
用例失败说明检索质量有问题，而不是 fixture 需要放宽——修改 fixture 的期望值必须
在报告中留下理由，否则等于把回归伪装成通过。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests/fixtures/memory_eval/golden_v1.json"

#: 报告里记录的评测器版本。任何指标定义变化都必须提升它，使历史报告不可直接比较。
EVALUATOR_VERSION = "memory-eval-v1"

ABILITY_LABELS = {
    "extraction": "信息提取",
    "temporal": "时序推理",
    "update": "知识更新",
    "multi_session": "多会话推理",
    "abstention": "弃答",
}


def load_golden_set(path: Path | None = None) -> dict[str, Any]:
    payload: Any = json.loads((path or FIXTURE_PATH).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("golden set 必须是 JSON 对象")
    return payload


@dataclass
class CaseResult:
    """One golden case. `mode` decides how recall is defined — the three modes
    are genuinely different invariants and must not be averaged together:

    - `answerable`   : 必须召回全部期望来源；
    - `abstention`   : 必须返回**空**（库里没有就该说没有）；
    - `forbidden_only`: 只约束「不得返回某来源」，不要求整条为空（例如问「现在」
                        时不得把已结束的事实当当前事实，但问题里的其它词合法匹配）。
    """

    case_id: str
    ability: str
    tier: str
    mode: str
    expected: tuple[str, ...]
    returned: tuple[str, ...]
    forbidden_hits: tuple[str, ...]
    missing: tuple[str, ...]
    passed: bool
    elapsed_ms: float
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def recall(self) -> float:
        if self.mode == "abstention":
            return 1.0 if not self.returned else 0.0
        if self.mode == "forbidden_only":
            return 0.0 if self.forbidden_hits else 1.0
        if not self.expected:
            return 0.0
        return (len(self.expected) - len(self.missing)) / len(self.expected)

    @property
    def citation_precision(self) -> float:
        """命中里属于期望来源的比例。弃答用例返回任何内容都算 0。"""
        if self.mode != "answerable":
            return 1.0 if not self.returned else 0.0
        if not self.returned:
            return 0.0
        hits = sum(1 for label in self.returned if label in self.expected)
        return hits / len(self.returned)


def _elapsed(fn: Any) -> tuple[Any, float]:
    started = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - started) * 1000.0


def evaluate_case(case: dict[str, Any], retrieve: Any) -> CaseResult:
    """Run one golden case through ``retrieve(question) -> list[str]`` (labels).

    ``retrieve`` 返回的是**来源标签**（fixture 里的 `label`），由调用方把
    `RAGHit.source_id` 映射回标签。这样评估层不依赖任何具体检索实现，
    因此同一份 golden set 可以对照词法路径、重排路径或未来向量路径。
    """
    expected = tuple(case.get("expected_sources", ()))
    forbidden = tuple(case.get("forbidden_sources", ()))
    returned, elapsed_ms = _elapsed(lambda: list(retrieve(case["question"])))
    returned_labels = tuple(returned)
    missing = tuple(label for label in expected if label not in returned_labels)
    forbidden_hits = tuple(label for label in forbidden if label in returned_labels)
    if case.get("expect_empty"):
        mode = "abstention"
        passed = not returned_labels
    elif not expected and forbidden:
        mode = "forbidden_only"
        passed = not forbidden_hits
    else:
        mode = "answerable"
        passed = not missing and not forbidden_hits
    return CaseResult(
        case_id=case["id"],
        ability=case["ability"],
        tier=case.get("tier", "contract"),
        mode=mode,
        expected=expected,
        returned=returned_labels,
        forbidden_hits=forbidden_hits,
        missing=missing,
        passed=passed,
        elapsed_ms=elapsed_ms,
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    retrieval = list(results)
    answerable = [result for result in results if result.mode == "answerable"]
    abstention = [result for result in results if result.mode == "abstention"]
    latencies = [result.elapsed_ms for result in results]
    by_ability: dict[str, dict[str, float]] = {}
    for result in results:
        ability_bucket = by_ability.setdefault(
            result.ability, {"cases": 0.0, "passed": 0.0, "recall_sum": 0.0}
        )
        ability_bucket["cases"] += 1
        ability_bucket["passed"] += 1 if result.passed else 0
        ability_bucket["recall_sum"] += result.recall
    for ability_bucket in by_ability.values():
        ability_cases = ability_bucket["cases"]
        ability_bucket["pass_rate"] = (
            ability_bucket["passed"] / ability_cases if ability_cases else 0.0
        )
        ability_bucket["mean_recall"] = (
            ability_bucket["recall_sum"] / ability_cases if ability_cases else 0.0
        )
    ability_labels = {name: ABILITY_LABELS.get(name, name) for name in by_ability}
    tiers: dict[str, dict[str, float]] = {}
    for tier_name in sorted({result.tier for result in results}):
        tier_cases = [result for result in results if result.tier == tier_name]
        tier_answerable = [result for result in tier_cases if result.mode == "answerable"]
        tier_abstention = [result for result in tier_cases if result.mode == "abstention"]
        tiers[tier_name] = {
            "cases": float(len(tier_cases)),
            "passed": float(sum(1 for result in tier_cases if result.passed)),
            "pass_rate": sum(1 for result in tier_cases if result.passed) / len(tier_cases),
            "retrieval_recall": (
                sum(result.recall for result in tier_answerable) / len(tier_answerable)
                if tier_answerable
                else 1.0
            ),
            "forbidden_hits": float(sum(len(result.forbidden_hits) for result in tier_cases)),
            "abstention_accuracy": (
                sum(1 for result in tier_abstention if result.passed) / len(tier_abstention)
                if tier_abstention
                else 1.0
            ),
        }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "tiers": tiers,
        "cases": len(results),
        "passed": sum(1 for result in results if result.passed),
        "pass_rate": (sum(1 for result in results if result.passed) / len(results))
        if results
        else 0.0,
        "retrieval_recall": (
            sum(result.recall for result in answerable) / len(answerable) if answerable else 0.0
        ),
        "citation_precision": (
            sum(result.citation_precision for result in retrieval) / len(retrieval)
            if retrieval
            else 0.0
        ),
        "forbidden_hits": sum(len(result.forbidden_hits) for result in results),
        "abstention_accuracy": (
            sum(1 for result in abstention if result.passed) / len(abstention)
            if abstention
            else 0.0
        ),
        "latency_ms": {
            "p50": median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
        "by_ability": by_ability,
        "ability_labels": ability_labels,
    }


def evaluate_extraction(detector: Any) -> dict[str, Any]:
    """评测确定性提取器的类别产出与守卫（不含任何模型调用）。

    分两层，与检索用例同一原则：

    - `contract`：规则**应当**覆盖的类别与守卫，必须全过；
    - `gap`：实测发现的能力缺口。刻意不为了让报告变绿而放宽 fixture——这些用例的
      期望值是产品要求，不是实现现状；它们作为 P2 提取精炼的输入清单保留在报告里。
    """
    golden = load_golden_set()
    failures: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    checked = contract = 0
    for case in golden.get("extraction_cases", []):
        checked += 1
        tier = case.get("tier", "contract")
        if tier == "contract":
            contract += 1
        inputs = detector(case["text"])
        categories = [item.extractor_category for item in inputs]
        expected = list(case.get("expect_categories", []))
        mismatch = categories != expected
        nondeterministic = False
        if case.get("assert_deterministic"):
            again = detector(case["text"])
            nondeterministic = [
                (item.summary, item.extractor_category, item.source_quote) for item in again
            ] != [(item.summary, item.extractor_category, item.source_quote) for item in inputs]
        if mismatch or nondeterministic:
            entry = {
                "case_id": case["id"],
                "tier": tier,
                "expected": expected,
                "actual": categories,
                "gap_reason": case.get("gap_reason"),
                "nondeterministic": nondeterministic,
            }
            (gaps if tier == "gap" else failures).append(entry)
    contract_passed = contract - len(failures)
    return {
        "cases": checked,
        "contract_cases": contract,
        "passed": contract_passed,
        "pass_rate": contract_passed / contract if contract else 0.0,
        "failures": failures,
        "known_gaps": gaps,
    }


def build_report(
    *,
    results: list[CaseResult],
    extraction: dict[str, Any],
    golden: dict[str, Any],
    model_version: str = "no-model(retrieval-only)",
) -> dict[str, Any]:
    """Assemble the versioned report. Never contains source text or credentials."""
    return {
        "fixture_version": golden.get("version"),
        "fixture_k": golden.get("k"),
        "model_version": model_version,
        "note": (
            "只评测检索层与确定性提取器，不调用任何模型；回答准确率需要真实 provider "
            "另行运行。失败用例的期望值不得为了让报告变绿而放宽。"
        ),
        "aggregate": aggregate(results),
        "extraction": extraction,
        "cases": [
            {
                "case_id": result.case_id,
                "ability": result.ability,
                "tier": result.tier,
                "mode": result.mode,
                "expected": list(result.expected),
                "returned": list(result.returned),
                "missing": list(result.missing),
                "forbidden_hits": list(result.forbidden_hits),
                "recall": result.recall,
                "citation_precision": result.citation_precision,
                "passed": result.passed,
                "elapsed_ms": round(result.elapsed_ms, 3),
            }
            for result in results
        ],
    }
