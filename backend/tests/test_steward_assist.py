"""Steward 模型辅助层测试（09-06 子任务 B；候选/排序/解释 + child run 审计）。

覆盖（design §6）：
- AC-1：开关默认全关 → 零调用零写入，行为与确定性基线一致；
- 开关矩阵：平台∧空间任一为假 → 不调用；
- AC-2：排序只应用严格排列（集合成员不变）；候选永不进卡；
- AC-3：审计行字段完整（space/job/policy_version/kind/digest/usage），
  且 assistant 三表（agent_sessions/messages/runs）零新增行（隔离）；
- AC-5：provider 不可用 / transport 失败 → degraded/failed 行 + 流水线照常出卡；
- 预算耗尽 → skipped 行且不再发起 transport 调用；
- 幂等（B4）：同 (job, kind, seq) 审计行存在即跳过；候选 digest 跨 job 去重；
- 解释：写入/截断/失败保持 NULL。
"""

import json

from sqlalchemy import select

from app import config
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.models.steward import ActionCard, StewardJob, StewardLlmCandidate, StewardModelCall
from app.services import steward_assist
from app.utils import timeutil
from app.utils.secretbox import encrypt_secret

from test_steward import _confirm, _emit_fact_event, _person, _run_job, _space


# ---- 造数辅助 ----


def _provider(db_session, *, name="assist-provider", model="gpt-5.6-sol"):
    row = AgentProvider(
        name=name,
        kind="openai_compatible",
        base_url="https://api.example.com/v1",
        secret_ciphertext=encrypt_secret("sk-assist-test"),
        allowed_models_json=[model],
        enabled=True,
        created_at=timeutil.utcnow(),
        updated_at=timeutil.utcnow(),
    )
    db_session.add(row)
    db_session.flush()
    return row


def _steward_setting(
    db_session,
    space,
    provider,
    *,
    model="gpt-5.6-sol",
    enabled=True,
    candidate=False,
    ranking=False,
    explanation=False,
):
    row = AgentSpaceProviderSetting(
        space_id=space.id,
        agent_kind="steward",
        provider_id=provider.id,
        model=model,
        cloud_allowed=True,
        enabled=enabled,
        assist_candidate=candidate,
        assist_ranking=ranking,
        assist_explanation=explanation,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _turn_on(monkeypatch, *, candidate=True, ranking=True, explanation=True):
    monkeypatch.setattr(config, "STEWARD_ASSIST_CANDIDATE", candidate)
    monkeypatch.setattr(config, "STEWARD_ASSIST_RANKING", ranking)
    monkeypatch.setattr(config, "STEWARD_ASSIST_EXPLANATION", explanation)


def _responses_fake(calls: list[dict], texts: list[str] | None = None):
    """openai-responses 形状的 fake transport；texts 依次取用（超出复用最后一个）。"""

    def transport(url, headers, payload, timeout):
        calls.append({"url": url, "payload": payload, "auth": headers.get("Authorization")})
        text = texts[min(len(calls), len(texts)) - 1] if texts else "[]"
        return {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    return transport


def _ranking_fake(calls: list[dict], *, mode: str = "reverse"):
    """动态排序 fake：从请求 payload 提取 card_ids，按 mode 生成排列。"""

    def transport(url, headers, payload, timeout):
        calls.append({"url": url, "payload": payload})
        user_content = payload["input"][-1]["content"]
        card_ids = [item["card_id"] for item in json.loads(user_content)]
        if mode == "reverse":
            order = list(reversed(card_ids))
        else:  # duplicate：非法排列（重复第一个、丢失最后一个）
            order = [card_ids[0], card_ids[0]]
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(order)}]}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    return transport


def _spouse_space(db_session, name: str):
    """lineage 空间 + 确认配偶事实（成员↔空间外 ref）：跑 job 产出 2 张卡。"""
    space = _space(db_session, name, kind="lineage")
    a = _person(db_session, space.id, f"{name}-a", gender="f")
    b = _person(db_session, space.id, f"{name}-b", gender="m", member=False, ref=True)
    fact = _confirm(db_session, "spouse", a.id, b.id, space_id=space.id)
    event = _emit_fact_event(db_session, fact)
    db_session.commit()
    return space, a, b, event


def _cards(db_session, space_id: int) -> list[ActionCard]:
    return list(db_session.scalars(select(ActionCard).where(ActionCard.space_id == space_id)))


def _calls(db_session, job_id: int) -> list[StewardModelCall]:
    return list(
        db_session.scalars(
            select(StewardModelCall)
            .where(StewardModelCall.job_id == job_id)
            .order_by(StewardModelCall.id)
        )
    )


# ---- AC-1：默认全关 ----


def test_default_off_is_behavior_equivalent(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-off")
    _turn_on(monkeypatch, candidate=False, ranking=False, explanation=False)

    summary, job = _run_job(db_session, space, event.id)

    assert summary["stats"]["cards_created"] == 2
    assert _calls(db_session, job.id) == []
    assert list(db_session.scalars(select(StewardLlmCandidate))) == []


# ---- 开关矩阵 ----


def test_platform_on_space_off_no_calls(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-plat")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider)  # 空间三开关全 False
    _turn_on(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _run_job(db_session, space, event.id)

    assert calls == []
    assert len(_cards(db_session, space.id)) == 2


def test_space_on_platform_off_no_calls(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-space")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True, ranking=True, explanation=True)
    _turn_on(monkeypatch, candidate=False, ranking=False, explanation=False)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    _run_job(db_session, space, event.id)

    assert calls == []


# ---- 候选：入池 + 校验 + 审计（AC-2/AC-3）----


def test_candidate_pool_validated_and_audited(db_session, monkeypatch) -> None:
    space, a, b, event = _spouse_space(db_session, "assist-cand")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    outsider = _person(db_session, None, "assist-outsider")
    payload_text = (
        "["
        f'{{"kind":"sibling","subject_user_id":{a.id},"object_user_id":{b.id},'
        '"rationale":"同姓分支"},'
        f'{{"kind":"sibling","subject_user_id":{a.id},"object_user_id":{outsider.id},'
        '"rationale":"越权"},'
        f'{{"kind":"sibling","subject_user_id":{a.id},"object_user_id":{a.id},'
        '"rationale":"自环"},'
        '{"kind":"sibling","subject_user_id":99999,"object_user_id":1,"rationale":"不存在"}'
        "]"
    )
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, [payload_text]))

    summary, job = _run_job(db_session, space, event.id)

    # 确定性建卡照常（候选绝不替代矩阵）
    assert summary["stats"]["cards_created"] == 2

    candidates = list(db_session.scalars(select(StewardLlmCandidate)))
    assert [
        (c.payload_json["subject_user_id"], c.payload_json["object_user_id"]) for c in candidates
    ] == [(a.id, b.id)]
    assert candidates[0].status == "proposed"

    audit = _calls(db_session, job.id)[0]
    assert audit.assist_kind == "candidate"
    assert audit.status == "succeeded"
    assert audit.space_id == space.id
    assert audit.policy_version == job.policy_version
    assert audit.provider_id == provider.id
    assert audit.model == "gpt-5.6-sol"
    assert len(audit.prompt_digest) == 64 and audit.prompt_chars > 0
    assert audit.total_tokens == 15


def test_candidate_unparseable_dropped_but_audited(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-cand-bad")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, ["不是 JSON"]))

    _run_job(db_session, space, event.id)

    assert list(db_session.scalars(select(StewardLlmCandidate))) == []
    job = db_session.scalar(select(StewardJob).where(StewardJob.space_id == space.id))
    assert _calls(db_session, job.id)[0].status == "succeeded"


# ---- 排序：严格排列才应用（AC-2）----


def test_ranking_applies_valid_permutation(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-rank")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, ranking=True)
    _turn_on(monkeypatch, candidate=False, explanation=False)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _ranking_fake(calls, mode="reverse"))

    summary, job = _run_job(db_session, space, event.id)
    assert summary["stats"]["cards_created"] == 2

    cards = sorted(_cards(db_session, space.id), key=lambda r: r.id)
    assert len(cards) == 2
    ranks = {int(card.id): card.presentation_rank for card in cards}
    # 严格排列：两卡均有 rank 且恰为 1..2；集合成员不变
    assert sorted(ranks.values()) == [1, 2]
    assert set(ranks) == {int(card.id) for card in cards}
    # reverse 语义：id 较大的卡 rank=1（先呈现）
    assert ranks[max(ranks)] == 1
    audit = _calls(db_session, job.id)
    assert audit and audit[0].assist_kind == "ranking" and audit[0].status == "succeeded"


def test_ranking_rejects_invalid_permutation(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-rank-bad")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, ranking=True)
    _turn_on(monkeypatch, candidate=False, explanation=False)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _ranking_fake(calls, mode="duplicate"))

    summary, job = _run_job(db_session, space, event.id)

    assert summary["stats"]["cards_created"] == 2
    assert all(card.presentation_rank is None for card in _cards(db_session, space.id))
    audit = _calls(db_session, job.id)
    assert audit and audit[0].status == "degraded"
    assert audit[0].error_code == "invalid_permutation"


# ---- 解释：写入 / 截断 / 失败兜底（AC-5）----


def test_explanation_written_and_truncated(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-explain")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, explanation=True)
    _turn_on(monkeypatch, candidate=False, ranking=False)
    long_text = "这是一段很长的解释。" * 100  # 远超 500 字
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, [long_text]))

    _run_job(db_session, space, event.id)

    cards = _cards(db_session, space.id)
    assert len(cards) == 2
    assert all(
        card.reason_text_llm is not None and len(card.reason_text_llm) == 500 for card in cards
    )


def test_explanation_failure_keeps_template_and_pipeline(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-explain-fail")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, explanation=True)
    _turn_on(monkeypatch, candidate=False, ranking=False)

    def boom(url, headers, payload, timeout):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(steward_assist, "_post_json", boom)

    summary, job = _run_job(db_session, space, event.id)

    assert summary["stats"]["cards_created"] == 2
    assert all(card.reason_text_llm is None for card in _cards(db_session, space.id))
    audit = _calls(db_session, job.id)
    assert len(audit) == 2
    assert all(row.status == "failed" and row.error_code == "RuntimeError" for row in audit)


# ---- AC-5：provider 不可用降级 ----


def test_provider_unavailable_degrades_pipeline_intact(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-degraded")
    provider = _provider(db_session)
    provider.enabled = False
    db_session.commit()
    _steward_setting(db_session, space, provider, explanation=True)  # 开关行在，通道不可用
    _turn_on(monkeypatch, candidate=False, ranking=False, explanation=True)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls))

    summary, job = _run_job(db_session, space, event.id)

    assert summary["stats"]["cards_created"] == 2
    assert calls == []
    audit = _calls(db_session, job.id)
    assert audit and audit[0].status == "degraded"
    assert audit[0].error_code == "provider_unavailable"


# ---- AC-3：与 Assistant 三表隔离 ----


def test_assist_never_touches_assistant_tables(db_session, monkeypatch) -> None:
    from app.models.agent import AgentMessage, AgentRun, AgentSession

    space, _a, _b, event = _spouse_space(db_session, "assist-isolate")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True, ranking=True, explanation=True)
    _turn_on(monkeypatch)
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake([]))

    _run_job(db_session, space, event.id)

    assert db_session.scalars(select(AgentSession)).first() is None
    assert db_session.scalars(select(AgentRun)).first() is None
    assert db_session.scalars(select(AgentMessage)).first() is None


# ---- 预算 ----


def test_budget_exhaustion_skips_remaining(db_session, monkeypatch) -> None:
    space, _a, _b, event = _spouse_space(db_session, "assist-budget")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, explanation=True)
    _turn_on(monkeypatch, candidate=False, ranking=False)
    monkeypatch.setattr(config, "STEWARD_ASSIST_MAX_MODEL_CALLS_PER_JOB", 1)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, ["解释文本"]))

    summary, job = _run_job(db_session, space, event.id)

    assert summary["stats"]["cards_created"] == 2
    assert len(calls) == 1  # 第二张卡起不再发起 transport 调用
    audit = _calls(db_session, job.id)
    assert audit[0].status == "succeeded"
    assert any(
        row.status == "skipped" and row.error_code == "budget_exhausted" for row in audit[1:]
    )


# ---- 幂等（B4）----


def test_reentry_same_job_skips_by_seq(db_session, monkeypatch) -> None:
    space, a, b, event = _spouse_space(db_session, "assist-retry")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True, ranking=True)
    _turn_on(monkeypatch, explanation=False)
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, "[]"))

    _summary, job = _run_job(db_session, space, event.id)
    first_count = len(calls)
    assert first_count == 2  # candidate + ranking

    steward_assist.run_assists(
        db_session,
        job=job,
        facts_brief=[],
        visible={a.id, b.id},
        cards=_cards(db_session, space.id),
    )
    assert len(calls) == first_count  # 同 (job, kind, seq) 已有审计行 → 不重打模型


def test_candidate_digest_dedupes_across_jobs(db_session, monkeypatch) -> None:
    space, a, b, event = _spouse_space(db_session, "assist-dedupe")
    provider = _provider(db_session)
    _steward_setting(db_session, space, provider, candidate=True)
    _turn_on(monkeypatch, ranking=False, explanation=False)
    payload_text = (
        f'[{{"kind":"sibling","subject_user_id":{a.id},"object_user_id":{b.id},'
        '"rationale":"同分支"}]'
    )
    calls: list[dict] = []
    monkeypatch.setattr(steward_assist, "_post_json", _responses_fake(calls, [payload_text]))

    _run_job(db_session, space, event.id)
    _run_job(db_session, space, event.id + 1000)

    assert len(list(db_session.scalars(select(StewardLlmCandidate)))) == 1


# ---- flags schema：assistant 维度拒绝 / steward 维度存储 ----


def test_assist_flags_rejected_for_assistant_kind(client, db_session) -> None:
    from conftest import auth_header, create_agent_fixture, login

    owner, space = create_agent_fixture(db_session, name="assist-flag-schema")
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    response = client.put(
        f"/api/spaces/{space.id}/model-settings",
        json={"agent_kind": "assistant", "enabled": False, "assist_ranking": True},
        headers=headers,
    )
    assert response.status_code == 422


def test_assist_flags_stored_for_steward_kind(client, db_session) -> None:
    from conftest import auth_header, create_agent_fixture, login

    owner, space = create_agent_fixture(db_session, name="assist-flag-store")
    provider = _provider(db_session)
    db_session.commit()
    headers = auth_header(login(client, owner.name, "123456").json())
    response = client.put(
        f"/api/spaces/{space.id}/model-settings",
        json={
            "agent_kind": "steward",
            "provider_id": provider.id,
            "model": "gpt-5.6-sol",
            "assist_candidate": True,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assist_candidate"] is True
    assert body["assist_ranking"] is False

    row = db_session.scalar(
        select(AgentSpaceProviderSetting).where(
            AgentSpaceProviderSetting.space_id == space.id,
            AgentSpaceProviderSetting.agent_kind == "steward",
        )
    )
    assert row.assist_candidate is True
