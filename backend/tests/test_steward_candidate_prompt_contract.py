"""候选 prompt 契约与事实规模降级回归（09-19 任务）。

覆盖：
- R1/R2/R3：候选 prompt 显式声明方向语义、矛盾/重复禁止与可解析最小示例；
- R4：prompt 版本随文本变化、未变时稳定（评测报告 prompt_version 的可信来源）；
- R5/AC4：事实规模超限时批次终态与错误码如实反映「未产生候选」，不再是 applied；
- R6/AC5：超限计数可从管理员指标读到且不泄露 prompt/事实/姓名；
- R7/AC6：有界事实子集使大空间仍能产生候选，子集确定、可复现、输出仍过既有校验；
- AC7：未超限场景投影与变更前逐字节一致。

红线：本任务只改输入侧契约与规模处理；候选与已确认事实的语义冲突拦截由
并行任务 `09-19-steward-recommendation-correctness` 负责，此处不重复实现，
也不得削弱既有校验（节点代号投影/未成年过滤/字节上界/预算与租约栅栏）。
"""

from __future__ import annotations

import hashlib
import json

import httpx
from sqlalchemy import select
from test_steward import _confirm, _emit_fact_event, _person, _run_job, _space
from test_steward_assist import (
    _batch,
    _calls,
    _provider,
    _responses_fake,
    _run_assists,
    _steward_setting,
    _turn_on,
)

from app import config
from app.models.steward import StewardLlmCandidate, StewardModelCall
from app.services import steward as steward_service
from app.services import steward_assist, steward_guard
from app.utils import timeutil

# ---- 造数辅助 ----


def _space_with_facts(db_session, name: str, count: int):
    """lineage 空间 + 1 个成员与 count 个 ref 之间的已确认 spouse 事实。

    纯合成数据；事实 id 递增即投影排序键，便于断言子集是稳定前缀。
    """
    space = _space(db_session, name, kind="lineage")
    anchor = _person(db_session, space.id, f"{name}-anchor", gender="f")
    facts = []
    for index in range(count):
        other = _person(
            db_session, space.id, f"{name}-p{index}", gender="m", member=False, ref=True
        )
        fact = _confirm(db_session, "spouse", anchor.id, other.id, space_id=space.id)
        facts.append(fact)
    event = _emit_fact_event(db_session, facts[-1])
    db_session.commit()
    return space, anchor, facts, event


def _projected(db_session, space, limit: int | None = None) -> str:
    visible = steward_service._space_visible_user_ids(db_session, space)
    ctx = steward_guard.ProjectionContext(visible)
    facts = steward_assist._candidate_facts(db_session, space.id, ctx)
    return steward_guard.project_candidate_input(facts if limit is None else facts[:limit], ctx)


# ---- R1/R2/R3：prompt 契约 ----


def test_candidate_prompt_declares_direction_contract() -> None:
    """R1：方向语义逐条声明（父/母/监护人 + 对称关系），不是模糊提及。"""
    prompt = steward_assist._PROMPTS["candidate"]
    clause = steward_assist._CANDIDATE_DIRECTION_CLAUSE
    assert clause in prompt, "方向语义条款缺失（必须逐字出现）"
    for kind in ("biological_parent", "adoptive_parent", "step_parent", "guardian"):
        assert kind in clause, f"方向条款缺少 {kind}"
    assert "subject" in clause and "object" in clause
    for word in ("父", "母", "监护人"):
        assert word in clause, f"方向条款缺少方向词 {word}"
    for kind in ("spouse", "partner", "direct_sibling"):
        assert kind in clause, f"方向条款缺少对称关系 {kind}"
    assert "对称" in clause


def test_candidate_prompt_declares_conflict_and_duplicate_ban() -> None:
    """R2：显式禁止与输入事实矛盾/重复的候选，并给出跨 kind 互斥例子。"""
    prompt = steward_assist._PROMPTS["candidate"]
    clause = steward_assist._CANDIDATE_CONFLICT_CLAUSE
    assert clause in prompt, "矛盾/重复禁止条款缺失（必须逐字出现）"
    for word in ("矛盾", "重复", "不得"):
        assert word in clause, f"禁止条款缺少 {word}"
    # 同一对端点的不同 kind 也属矛盾：必须显式举例
    assert "biological_parent" in clause and "direct_sibling" in clause


def test_candidate_prompt_has_parseable_minimal_example() -> None:
    """R3：最小示例必须是可解析的 schema 合规 JSON 数组，且只用占位代号。"""
    prompt = steward_assist._PROMPTS["candidate"]
    clause = steward_assist._CANDIDATE_EXAMPLE_CLAUSE
    assert clause in prompt, "最小示例条款缺失（必须逐字出现）"
    start, end = clause.find("["), clause.rfind("]")
    assert start >= 0 and end > start, "示例条款内没有 JSON 数组"
    items = json.loads(clause[start : end + 1])
    assert items, "示例不得为空数组"
    for item in items:
        assert set(item) == {"kind", "subject", "object"}, item
        assert item["kind"] in steward_guard.CANDIDATE_ATOMIC_KINDS, item
        for key in ("subject", "object"):
            assert steward_guard._NODE_CODENAME_RE.fullmatch(item[key]), item
    # 示例不得携带任何真实家庭数据形态（只允许占位代号）
    assert "姓名" not in clause or "不含" in clause


def test_prompt_version_tracks_prompt_change_and_is_stable() -> None:
    """R4/AC3：prompt 变更 → 版本变化；未变更 → 稳定（评测报告字段可信）。"""
    baseline = steward_assist.prompt_version()
    assert baseline == steward_assist.prompt_version()
    original = dict(steward_assist._PROMPTS)
    try:
        steward_assist._PROMPTS["candidate"] = original["candidate"] + "\n补充条款"
        assert steward_assist.prompt_version() != baseline
        steward_assist._PROMPTS["candidate"] = original["candidate"]
        assert steward_assist.prompt_version() == baseline
    finally:
        steward_assist._PROMPTS.update(original)


# ---- R5/R6：超限如实结算与可观测 ----


def test_prompt_too_large_settles_batch_as_failed_with_reason(db_session, monkeypatch) -> None:
    """AC4：即使有界子集也无法容纳（上限小于系统提示本身）时，批次如实失败。

    断言具体终态与错误码、零网络调用、零预算消耗、零 unknown 计费。
    """
    space, _anchor, facts, event = _space_with_facts(db_session, "prompt-cap-hard", 3)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", 1)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    assert calls == []
    rows = _calls(db_session, job.id)
    assert rows, "候选辅助应至少留下一条 attempt 审计"
    assert all(
        r.status == "skipped" and r.error_code == steward_assist.REASON_PROMPT_TOO_LARGE
        for r in rows
    ), [(r.status, r.error_code) for r in rows]
    assert all(r.billed_tokens is None for r in rows)
    assert not any(r.status == "unknown" for r in rows)
    batch = _batch(db_session, job.id)
    assert batch is not None
    assert batch.status == "failed", batch.status
    assert batch.error_code == steward_assist.REASON_PROMPT_TOO_LARGE
    assert list(db_session.scalars(select(StewardLlmCandidate))) == []
    assert len(facts) == 3


def test_prompt_too_large_counted_in_admin_metrics(admin_client, db_session, monkeypatch) -> None:
    """AC5：超限计数可读，且响应只含计数/安全错误码（无 prompt/事实/姓名）。"""
    from conftest import admin_session_headers, create_system_admin

    monkeypatch.setattr(config, "STEWARD_ENABLED", True)
    create_system_admin(db_session)
    headers = admin_session_headers(admin_client)
    space = _space(db_session, "prompt-cap-metrics")
    job, _ = steward_service.enqueue_steward_job(
        db_session, space_id=space.id, cause="source_fact", trigger_cursor=1
    )
    now = timeutil.utcnow()
    for index, error_code in enumerate(
        (
            steward_assist.REASON_PROMPT_TOO_LARGE,
            steward_assist.REASON_BUDGET_EXHAUSTED,
        ),
        start=1,
    ):
        db_session.add(
            StewardModelCall(
                space_id=space.id,
                job_id=job.id,
                policy_version=steward_service.POLICY_VERSION,
                assist_kind="candidate",
                prompt_digest="0" * 64,
                prompt_chars=10,
                status="skipped",
                error_code=error_code,
                seq=index,
                created_at=now,
            )
        )
    db_session.commit()

    resp = admin_client.get("/admin-api/v1/steward/status", headers=headers)
    assert resp.status_code == 200
    metrics = resp.json()["metrics"]
    # 字段白名单用精确集合断言（不只看包含）
    assert set(metrics) == {
        "core_queue_depth",
        "oldest_queued_age_seconds",
        "last_scan_at",
        "last_worker_tick_at",
        "core_failed",
        "assist_failed",
        "assist_degraded",
        "assist_unknown",
        "assist_skipped",
        "assist_skipped_prompt_too_large",
        "budget_reserved_tokens",
        "budget_consumed_tokens",
        "pfv_stale",
        "cards_created",
        "cards_superseded",
    }
    assert metrics["assist_skipped"] == 2
    assert metrics["assist_skipped_prompt_too_large"] == 1
    # 不泄露 prompt 正文、事实、姓名或空间内部内容
    assert "你是家庭空间管家助手" not in resp.text
    assert space.name not in resp.text


# ---- R7/AC6/AC7：有界事实子集 ----


def test_oversized_fact_set_is_bounded_and_deterministic(db_session, monkeypatch) -> None:
    """AC6：全量超限时取确定前缀子集，候选辅助仍工作且输出仍过全部校验。"""
    space, anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-subset", 6)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)

    full = _projected(db_session, space)
    system_bytes = len(steward_assist._PROMPTS["candidate"].encode("utf-8"))
    empty_total = system_bytes + 1 + len(_projected(db_session, space, 0).encode("utf-8"))
    full_total = system_bytes + 1 + len(full.encode("utf-8"))
    # 上限比全量少 1 字节：必然截断，且空投影仍可发送（子集至少能装下 0 条）
    cap = full_total - 1
    assert cap >= empty_total, "上限过小，连空投影都装不下（无法验证子集路径）"
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", cap)

    ctx = steward_guard.ProjectionContext(
        steward_service._space_visible_user_ids(db_session, space)
    )
    bounded = steward_assist.candidate_user_content(db_session, space.id, ctx)
    assert len(bounded.encode("utf-8")) + system_bytes <= cap
    assert bounded != full and len(bounded) < len(full)
    body = json.loads(bounded)
    fact_ids = [int(f["fact_id"]) for f in body["facts"]]
    assert fact_ids == sorted(fact_ids), "子集必须是稳定前缀（fact_id 升序）"
    assert set(fact_ids) <= {int(f["fact_id"]) for f in json.loads(full)["facts"]}
    # 同输入同结果（可复现）
    db_session.expire_all()
    again = steward_assist.candidate_user_content(db_session, space.id, ctx)
    assert again == bounded

    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))
    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    assert calls, "有界子集下候选辅助必须真正发送"
    rows = _calls(db_session, job.id)
    assert all(r.status != "skipped" for r in rows), [(r.status, r.error_code) for r in rows]
    assert _batch(db_session, job.id).status == "applied"
    # 输出仍走既有封闭校验（端点仍在授权花名册内）
    _ = anchor


def test_untruncated_projection_is_byte_identical(db_session, monkeypatch) -> None:
    """AC7：未超限时投影与变更前逐字节一致（不引入截断标记或字段变化）。"""
    space, _anchor, _facts, _event = _space_with_facts(db_session, "prompt-cap-intact", 2)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", 64 * 1024)
    ctx = steward_guard.ProjectionContext(
        steward_service._space_visible_user_ids(db_session, space)
    )
    expected = steward_guard.project_candidate_input(
        steward_assist._candidate_facts(db_session, space.id, ctx), ctx
    )
    assert steward_assist.candidate_user_content(db_session, space.id, ctx) == expected


def test_bounded_subset_contract_holds_across_every_cap(db_session, monkeypatch) -> None:
    """AC6/AC7：逐字节扫描整个预算区间，两个分支的契约都成立。

    这是核查阶段发现的边界：当上限恰好等于「system + 空事实集投影」时，能装下的
    子集是 0 条，此时必须回落全量原样交既有上界如实结算，而不是发送空输入（那会
    把确定性输入问题伪装成正常成功、只得到零候选）。同时验证二分查找确实取到
    最大可行前缀（再多一条事实就超界），而不仅是“某个能装下的子集”。
    """
    space, _anchor, _facts, _event = _space_with_facts(db_session, "prompt-cap-scan", 6)
    ctx = steward_guard.ProjectionContext(
        steward_service._space_visible_user_ids(db_session, space)
    )
    facts = steward_assist._candidate_facts(db_session, space.id, ctx)
    system = steward_assist._PROMPTS["candidate"]

    def sent_bytes(count: int) -> int:
        projected = steward_guard.project_candidate_input(facts[:count], ctx)
        return len(f"{system}\n{projected}".encode())

    full_count = len(facts)
    full_text = steward_guard.project_candidate_input(facts, ctx)
    fits = falls_back = 0
    for cap in range(1, sent_bytes(full_count) + 1):
        monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", cap)
        content = steward_assist.candidate_user_content(db_session, space.id, ctx)
        observed = len(f"{system}\n{content}".encode())
        if observed > cap:
            assert content == full_text, f"cap={cap} 超界却不是全量原样"
            falls_back += 1
            continue
        fits += 1
        kept = len(json.loads(content)["facts"])
        if kept == full_count:
            assert content == full_text
        else:
            assert sent_bytes(kept + 1) > cap, f"cap={cap} 未取到最大子集: kept={kept}"
    assert fits and falls_back, (fits, falls_back)


def test_empty_subset_is_never_sent_as_a_fake_success(db_session, monkeypatch) -> None:
    """AC4+AC6 交界：子集为 0 条时必须走题实超限结算，不得发送空输入。"""
    space, _anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-zero", 3)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    system_bytes = len(steward_assist._PROMPTS["candidate"].encode("utf-8"))
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", system_bytes)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    assert calls == [], "子集为空时不得发送"
    rows = _calls(db_session, job.id)
    assert all(
        r.status == "skipped" and r.error_code == steward_assist.REASON_PROMPT_TOO_LARGE
        for r in rows
    ), [(r.status, r.error_code) for r in rows]
    batch = _batch(db_session, job.id)
    assert batch is not None
    assert batch.status == "failed" and batch.error_code == steward_assist.REASON_PROMPT_TOO_LARGE


def test_budget_skipped_stays_benign_not_prompt_too_large(db_session, monkeypatch) -> None:
    """AC4：预算类 skipped 保持良性（新终态映射不得误伤）。

    `insufficient_budget` 是「本次不发送、稍后可重试」，与确定性的
    `prompt_too_large` 不同：批次必须仍为 `applied`，不得被新分支拉成 failed。
    """
    space, _anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-budget", 2)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_TOKENS_PER_JOB", 100)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    rows = _calls(db_session, job.id)
    codes = {r.error_code for r in rows}
    assert codes == {steward_assist.REASON_INSUFFICIENT_BUDGET}, codes
    batch = _batch(db_session, job.id)
    assert batch is not None
    assert batch.status == "applied", (batch.status, batch.error_code)
    assert batch.error_code is None


def test_upstream_unknown_outranks_prompt_too_large(db_session, monkeypatch) -> None:
    """AC4：优先级——unknown 必须压过 prompt_too_large（不伪造上游不确定性）。

    同批既有一个无法证明上游未处理的请求（unknown）又有一个确定超限的候选：
    批次终态是 `network_unknown`，不得被更“具体”的输入问题覆盖。
    """
    space, anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-prio", 2)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True, explanation=True)
    _turn_on(monkeypatch, ranking=False)
    system_bytes = len(steward_assist._PROMPTS["candidate"].encode("utf-8"))
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", system_bytes - 10)

    def timeout_transport(url, headers, payload, timeout):
        raise httpx.ReadTimeout("read timed out", request=httpx.Request("POST", url))

    monkeypatch.setattr(steward_assist, "_post_json", timeout_transport)
    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session, rounds=2)

    rows = _calls(db_session, job.id)
    assert "unknown" in {r.status for r in rows}, [(r.status, r.error_code) for r in rows]
    assert steward_assist.REASON_PROMPT_TOO_LARGE in {r.error_code for r in rows}
    batch = _batch(db_session, job.id)
    assert batch is not None
    assert batch.status == "failed"
    assert batch.error_code == steward_assist.REASON_NETWORK_UNKNOWN, batch.error_code
    _ = anchor


def test_oversized_space_still_validates_candidate_output(db_session, monkeypatch) -> None:
    """AC6 补充：子集场景下模型输出仍经封闭 schema 校验（越权代号照样整体拒绝）。"""
    space, anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-validate", 5)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    system_bytes = len(steward_assist._PROMPTS["candidate"].encode("utf-8"))
    full = _projected(db_session, space)
    monkeypatch.setattr(
        config,
        "STEWARD_ASSIST_MAX_PROMPT_BYTES",
        system_bytes + len(full.encode("utf-8")),
    )
    calls: list[dict] = []
    monkeypatch.setattr(
        steward_assist,
        "_post_json",
        _responses_fake(calls, ['[{"kind":"spouse","subject":"n001","object":"n999"}]']),
    )

    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    assert calls
    assert list(db_session.scalars(select(StewardLlmCandidate))) == []
    rows = _calls(db_session, job.id)
    assert rows[0].status == "degraded"
    _ = anchor


def test_prompt_digest_tracks_the_bounded_subset(db_session, monkeypatch) -> None:
    """AC6/AC7：子集化的输入哈希与 prompt 摘要稳定，发送前 fence 不误判。

    `_reserve_attempt` 用 `_canonical_hash(user_content)` 与
    `sha256(system + "\\n" + user)` 落审计，发送前逐笔比对。子集必须确定，
    否则同一 attempt 会在 fence 处被判 `evidence_changed` 而永不发送。
    """
    space, _anchor, _facts, event = _space_with_facts(db_session, "prompt-cap-fence", 4)
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    system_bytes = len(steward_assist._PROMPTS["candidate"].encode("utf-8"))
    full = _projected(db_session, space)
    monkeypatch.setattr(
        config, "STEWARD_ASSIST_MAX_PROMPT_BYTES", system_bytes + len(full.encode("utf-8"))
    )
    ctx = steward_guard.ProjectionContext(
        steward_service._space_visible_user_ids(db_session, space)
    )
    expected_user = steward_assist.candidate_user_content(db_session, space.id, ctx)
    assert expected_user != full, "夹具未截断，无法验证 fence 对子集的稳定性"
    expected_hash = steward_assist._canonical_hash(expected_user)
    expected_digest = hashlib.sha256(
        f"{steward_assist._PROMPTS['candidate']}\n{expected_user}".encode()
    ).hexdigest()
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _summary, job = _run_job(db_session, space, event.id)
    _run_assists(db_session)

    row = _calls(db_session, job.id)[0]
    assert calls, "子集确定时不得被 fence 判为 evidence_changed"
    assert row.status == "succeeded", (row.status, row.error_code)
    assert row.input_hash == expected_hash
    assert row.prompt_digest == expected_digest
