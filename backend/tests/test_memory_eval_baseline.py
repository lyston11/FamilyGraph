"""P0 检索质量基线：golden set + 指标 + 回归门。

## 这套测试的三条职责

1. **建立基线**：在隔离库上把 golden set 跑过真实 `search_rag` 与
   `ContextBuilder`，把分数与代码版本一起写进报告；
2. **当回归门**：分数跌破阈值即失败——没有这个门，任何检索改动都无法证伪；
3. **当取代语义的量化证据**：`forbidden_hits` 直接衡量「被取代的旧事实是否
   仍然被检索到」。P1 的行为测试证明它不会；这条测试证明它在**完整 golden set**
   上也不会。

## 为什么门阈值不是「越高越好」

阈值取的是**当前真实水平**，不是理想水平：本文件的作用是防止静默退化，而不是
宣称质量已经足够。`MIN_*` 常量旁边的注释记录了取值的理由。任何放宽都必须写明
为什么是 fixture 的期望错了，而不是实现退化了。
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from app.services import memory_eval, memory_extractor, memory_rag
from app.utils import timeutil
from conftest import create_agent_fixture

#: 报告落盘位置。默认写入 artifacts/（与 migration-proof 同一约定，便于归档比对）。
REPORT_PATH = Path(
    os.environ.get(
        "MEMORY_EVAL_REPORT_PATH",
        str(Path(__file__).resolve().parents[2] / "artifacts" / "memory-eval" / "baseline.json"),
    )
)

#: 回归门分两层，因为「当前做不到」和「不允许退化」是两件事。
#:
#: **contract 层必须 100%**：取代/有效区间语义、弃答、单跳提取与时序。这些用例
#: 表达的是**合同**，不是质量目标；允许部分通过等于允许静默退化。
#:
#: **quality 层曾记录基线但不设门**：多会话聚合（一个问题要同时召回两条记忆）
#: 在 P0 时做不到——实测 `multi-session-story` 5 条只召回 1 条期望来源。
#:
#: **P3 起已提升为硬门**：确定性重排（查询词重叠度 + 分支共识 + 来源类别）把
#: quality 层提到 3/3、整体 recall 1.000。这正是「先记录基线、再由改进提升为硬门」
#: 的用法——如果当初把做不到的用例设成硬门，它会立刻失败从而被绕过。
MIN_CONTRACT_PASS_RATE = 1.0
MIN_CONTRACT_RECALL = 1.0
MIN_CONTRACT_ABSTENTION_ACCURACY = 1.0
#: quality 层现在是硬门（2026-10-10 起）。若某天某条质量用例回归，正确做法是修实现
#: 或写明为何该期望错了，而不是把它降回「只记录」。
MIN_QUALITY_PASS_RATE = 1.0
MIN_QUALITY_RECALL = 1.0
#: 提取器类别产出：确定性规则，没有理由不全部通过。
MIN_EXTRACTION_PASS_RATE = 1.0
#: 取代语义的直接证伪指标：一个都不允许（两层合计）。
MAX_FORBIDDEN_HITS = 0


def _enable(db):
    from app.models.platform_features import PlatformFeatureConfig
    from app.utils import timeutil as tu

    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=tu.utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def _confirm(db, owner, *, summary, scope="private", space_id=None, sensitivity="normal"):
    candidate = memory_rag.propose_candidate(
        db,
        author_account_id=owner.account.id,
        source={"kind": "manual"},
        source_quote=summary,
        summary=summary,
        suggested_scope=scope,
        purpose="golden set",
        sensitivity=sensitivity,
    )
    return memory_rag.confirm_candidate(
        db,
        candidate_id=candidate.id,
        confirmer=owner,
        confirmer_account=owner.account,
        scope=scope,
        space_id=space_id,
    )


def _seed(db, owner, space, golden) -> dict[str, int]:
    """Materialize the golden set. Supersede links are applied as declared."""
    labels: dict[str, int] = {}
    supersedes: list[tuple[int, int]] = []
    for entry in golden["memories"]:
        memory = _confirm(
            db,
            owner,
            summary=entry["text"],
            sensitivity=entry.get("sensitivity", "normal"),
        )
        labels[entry["label"]] = memory.id
        if "supersedes" in entry:
            supersedes.append((labels[entry["supersedes"]], memory.id))
    # 已结束的时间窗（`expired_labels`）：不是取代，而是「曾经有效、现在不是当前事实」。
    for label in golden.get("expired_labels", []):
        row = db.get(memory_rag.Memory, labels[label], populate_existing=True)
        row.valid_from = timeutil.utcnow() - timedelta(days=400)
        row.valid_to = timeutil.utcnow() - timedelta(days=30)
    db.flush()
    for old_id, new_id in supersedes:
        memory_rag.supersede_memory(
            db, memory_id=old_id, account_id=owner.account.id, by_memory_id=new_id
        )
    db.commit()
    return labels


def _retriever(db, owner, space, labels):
    """Return ``question -> [label]`` using the real ``search_rag`` path."""
    reverse = {str(memory_id): label for label, memory_id in labels.items()}

    def retrieve(question: str) -> list[str]:
        hits = memory_rag.search_rag(
            db,
            actor=owner,
            account=owner.account,
            space_id=space.id,
            query=question,
            limit=memory_eval.load_golden_set()["k"],
            for_model=False,
        )
        return [reverse[hit.source_id] for hit in hits if hit.source_id in reverse]

    return retrieve


@pytest.fixture()
def golden():
    return memory_eval.load_golden_set()


def test_golden_set_shape_is_frozen(golden):
    """fixture 结构本身是合同：缺字段会让评估静默变成「通过」。"""
    assert golden["version"] == 1
    assert golden["k"] >= 1
    labels = [entry["label"] for entry in golden["memories"]]
    assert len(labels) == len(set(labels)), "标签必须唯一（取代关系按标签引用）"
    for entry in golden["memories"]:
        if "supersedes" in entry:
            assert entry["supersedes"] in labels, entry
    abilities = {case["ability"] for case in golden["cases"]}
    assert abilities == {
        "extraction",
        "temporal",
        "update",
        "multi_session",
        "abstention",
    }, "五个能力维度必须都有覆盖（LongMemEval 定义）"
    tiers = {case.get("tier") for case in golden["cases"]}
    assert tiers == {"contract", "quality"}, "两层都必须有覆盖"
    for case in golden["cases"]:
        assert case["question"].strip()
        if case.get("expect_empty"):
            assert not case.get("expected_sources"), "弃答用例不得声明期望来源"
        elif case.get("forbidden_sources"):
            # 「不得返回某来源」是合法用例：它只约束一条不变量，不要求整条为空。
            assert case["tier"] == "contract", "禁止来源的用例属于合同层"
        else:
            assert case.get("expected_sources"), "非弃答用例必须声明期望来源"


def test_retrieval_baseline_and_regression_gate(db_session, golden):
    """跑完整 golden set：分数、取代语义证伪指标、延迟一起落盘。"""
    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="memory-eval")
    labels = _seed(db_session, owner, space, golden)
    retrieve = _retriever(db_session, owner, space, labels)

    results = [memory_eval.evaluate_case(case, retrieve) for case in golden["cases"]]
    extraction = memory_eval.evaluate_extraction(memory_extractor.rule_detector)
    report = memory_eval.build_report(results=results, extraction=extraction, golden=golden)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    aggregate = report["aggregate"]
    tiers = aggregate["tiers"]
    failed = [
        {
            "case_id": result.case_id,
            "tier": result.tier,
            "missing": list(result.missing),
            "forbidden": list(result.forbidden_hits),
            "returned": list(result.returned),
        }
        for result in results
        if not result.passed
    ]
    contract_failed = [item for item in failed if item["tier"] == "contract"]

    # 取代语义的**直接证伪指标**：被取代的旧事实一次都不允许出现（两层合计）。
    assert (
        aggregate["forbidden_hits"] <= MAX_FORBIDDEN_HITS
    ), f"被取代的旧事实进入了检索结果：{failed}"
    # contract 层：100%，不允许余量。
    assert (
        tiers["contract"]["pass_rate"] >= MIN_CONTRACT_PASS_RATE
    ), f"合同层用例失败：{contract_failed}"
    assert (
        tiers["contract"]["retrieval_recall"] >= MIN_CONTRACT_RECALL
    ), f"合同层召回失败：{contract_failed}"
    assert (
        tiers["contract"]["abstention_accuracy"] >= MIN_CONTRACT_ABSTENTION_ACCURACY
    ), f"弃答失败：{contract_failed}"
    # quality 层：P3 起同为硬门。
    assert tiers["quality"]["pass_rate"] >= MIN_QUALITY_PASS_RATE, f"质量层退化：{failed}"
    assert tiers["quality"]["retrieval_recall"] >= MIN_QUALITY_RECALL, (
        f"质量层召回退化：{failed}"
    )
    assert extraction["pass_rate"] >= MIN_EXTRACTION_PASS_RATE, extraction["failures"]


def test_context_builder_uses_the_same_retrieval_result(db_session, golden):
    """端到端一致性：ContextBuilder 纳入的来源必须与 search_rag 的命中一致。

    这条测试防的是「检索质量测好了、但模型上下文走了另一条路径」——那会让
    P0 的分数与用户实际看到的内容脱节。
    """
    from app.services import context_builder

    _enable(db_session)
    owner, space = create_agent_fixture(db_session, name="memory-eval-context")
    labels = _seed(db_session, owner, space, golden)
    retrieve = _retriever(db_session, owner, space, labels)

    for case in golden["cases"]:
        if case.get("expect_empty"):
            continue
        built = context_builder.ContextBuilder(db_session).build(
            actor=owner,
            space_id=space.id,
            agent_kind="assistant",
            query=case["question"],
        )
        included = [source.source_id for source in built.sources]
        # 合同层的**全部**期望来源必须进入上下文（含禁止项），质量层只断言禁止项。
        if case.get("tier") == "contract":
            for label in case["expected_sources"]:
                assert str(labels[label]) in included, (
                    f"{case['id']}: 期望来源 {label} 未进入上下文"
                    f"（检索命中：{retrieve(case['question'])}）"
                )
        for label in case.get("forbidden_sources", ()):
            assert (
                str(labels[label]) not in included
            ), f"{case['id']}: 被取代/已失效的来源 {label} 进入了上下文"
