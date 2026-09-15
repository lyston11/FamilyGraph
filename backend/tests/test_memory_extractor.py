"""Deterministic memory extractor tests (2026-09-15 audit fix).

两层验证：
1. 纯函数 rule_detector——确定性、类别命中/不命中、上限；
2. settle 集成——成功结算产候选（经 propose_candidate 全链路，含 agent_message
   来源全等校验与幂等键）、MEMORY 关闭时 settle 不受影响、重复提取不重复。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.memory import MemoryCandidate
from app.services import agent_queue
from app.services.memory_extractor import (
    EXTRACTOR_VERSION,
    MAX_CANDIDATES_PER_MESSAGE,
    extract_after_settle,
    rule_detector,
    rule_detector_with_stats,
)
from conftest import (
    create_agent_fixture,
    create_agent_message,
    create_agent_session,
)


def _summaries(items):
    return [item.summary for item in items]


# ---- 纯函数 ----


def test_detector_birthday_hit():
    text = "对了，我妈妈的生日是 3 月 5 号，别忘了"
    items = rule_detector(text)
    assert _summaries(items) == ["生日：3月5日"]
    assert items[0].suggested_scope == "private"
    assert items[0].sensitivity == "normal"
    assert items[0].purpose
    # source_quote 必须是完整原文（agent_message 全等校验）
    assert items[0].source_quote == text


def test_detector_birthday_lunar_prefix():
    items = rule_detector("奶奶农历生日是 8 月 15 日")
    assert _summaries(items) == ["生日：8月15日（农历）"]


def test_detector_birthday_requires_context_word():
    # 只有日期没有「生日」语境词 → 不命中
    assert rule_detector("我们 3 月 5 号开家长会") == []


def test_detector_birthday_rejects_invalid_date():
    assert rule_detector("他生日是 13 月 40 号") == []


def test_detector_dietary_is_sensitive():
    items = rule_detector("小侄子对花生过敏，别给他吃")
    assert len(items) == 1
    assert items[0].sensitivity == "sensitive"
    assert items[0].summary.startswith("饮食禁忌：")


def test_detector_occupation():
    assert _summaries(rule_detector("我舅舅是医生")) == ["职业：医生"]
    assert _summaries(rule_detector("大伯在做工程师")) == ["职业：工程师"]
    # 无连接词不命中
    assert rule_detector("医生说我得多喝水") == []


def test_detector_residence():
    assert _summaries(rule_detector("我们一家住在成都")) == ["居住地：成都"]
    assert _summaries(rule_detector("奶奶家在绍兴县")) == ["居住地：绍兴县"]


def test_detector_preference():
    assert _summaries(rule_detector("爷爷最喜欢钓鱼")) == ["偏好：最喜欢钓鱼"]
    assert _summaries(rule_detector("妹妹怕狗")) == ["偏好：怕狗"]


def test_detector_caps_candidates_and_deterministic():
    text = "我妈妈生日是 3 月 5 号，她对海鲜过敏，爸爸是老师，我们住在成都，" "爷爷最喜欢钓鱼"
    first = rule_detector(text)
    second = rule_detector(text)
    assert first == second  # 确定性：逐字节相同
    assert len(first) == MAX_CANDIDATES_PER_MESSAGE
    # 类别顺序：生日 > 纪念日 > 饮食 > 职业 > 学校（居住/偏好被截断）
    assert _summaries(first) == ["生日：3月5日", "饮食禁忌：她对海鲜过敏", "职业：老师"]
    assert first[1].sensitivity == "sensitive"
    # 超限丢弃计数可观测（R1）
    items, dropped = rule_detector_with_stats(text)
    assert items == first and dropped == 2


def test_detector_anniversary_and_school():
    assert _summaries(rule_detector("我们结婚纪念日是 5 月 1 日")) == ["纪念日：5月1日"]
    assert _summaries(rule_detector("他在北京大学上学")) == ["学校：北京大学"]
    # 无校名的「读大学」不产卡（无具体信息量）
    assert rule_detector("他在读大学") == []
    assert _summaries(rule_detector("我妹妹是学生")) == ["职业：学生"]


def test_detector_negation_guard():
    # 否定/习语不得产出与陈述相反的候选
    assert rule_detector("我不喜欢吃香菜") == []
    assert rule_detector("我不喜欢下雨天") == []
    assert rule_detector("爷爷不喜欢钓鱼") == []
    assert rule_detector("哪怕下雨也要去") == []
    assert rule_detector("我不吃亏") == []
    # 肯定句仍命中
    assert _summaries(rule_detector("奶奶最喜欢跳广场舞")) == ["偏好：最喜欢跳广场舞"]


def test_detector_empty_and_noise():
    assert rule_detector("") == []
    assert rule_detector("   ") == []
    assert rule_detector("今天天气不错") == []
    # 超长输入被截断且不抛错
    long_text = "我妈妈生日是 3 月 5 号。" + "废话" * 10_000
    assert _summaries(rule_detector(long_text)) == ["生日：3月5日"]


# ---- settle 集成 ----


def _settled_run_with_text(db_session, text, name):
    user, space = create_agent_fixture(db_session, name=name)
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session, content={"text": text})
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.id == run.id
    settled = agent_queue.settle_run(db_session, run, status="succeeded")
    assert settled.status == "succeeded"
    return user, session, message, run


def test_settle_success_produces_pending_candidate(db_session):
    user, _session, message, _run = _settled_run_with_text(
        db_session, "我妈妈的生日是 3 月 5 号，记一下", "memex-birthday"
    )
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "pending"
    assert row.summary == "生日：3月5日"
    assert row.suggested_scope == "private"
    assert row.extractor_version == EXTRACTOR_VERSION
    # agent_message 来源链路完整：原文与消息全文一致
    assert row.source_message_id == message.id
    assert row.source_quote == "我妈妈的生日是 3 月 5 号，记一下"
    assert row.source_kind == "agent_message"


def test_settle_dietary_candidate_is_sensitive(db_session):
    user, _session, _message, _run = _settled_run_with_text(
        db_session, "提醒一下，小侄子对花生过敏", "memex-dietary"
    )
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert len(rows) == 1 and rows[0].sensitivity == "sensitive"


def test_settle_noise_produces_no_candidates(db_session):
    user, _session, _message, _run = _settled_run_with_text(
        db_session, "今天天气不错", "memex-noise"
    )
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


def test_extraction_idempotent_on_repeat_call(db_session):
    user, _session, message, run = _settled_run_with_text(
        db_session, "我妈妈的生日是 3 月 5 号", "memex-idem"
    )
    # 直接重复调用提取钩子（模拟重驱）：幂等键挡住重复
    again = extract_after_settle(db_session, run)
    assert again == 1  # replay 命中同候选
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert len(rows) == 1
    # 幂等键与消息、类别绑定
    assert rows[0].idempotency_key == f"extract:{message.id}:birthday:0"


def test_settle_survives_memory_disabled(db_session, monkeypatch):
    from app.services import memory_extractor as me

    monkeypatch.setattr(me.platform_features, "is_memory_enabled", lambda db: False, raising=True)
    # MEMORY 关闭：settle 仍成功、无候选
    user, _session, _message, run = _settled_run_with_text(
        db_session, "我妈妈的生日是 3 月 5 号", "memex-disabled"
    )
    assert run.status == "succeeded"
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


def test_extract_after_settle_swallows_errors(db_session):
    # run.message_id 为 None：直接短路返回 0，不抛错
    class _FakeRun:
        id = 999
        message_id = None
        session_id = 1

    assert extract_after_settle(db_session, _FakeRun()) == 0  # type: ignore[arg-type]


def test_settle_failed_produces_no_candidates(db_session):
    user, space = create_agent_fixture(db_session, name="memex-failed")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session, content={"text": "我妈妈生日是 3 月 5 号"})
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.id == run.id
    settled = agent_queue.settle_run(db_session, run, status="failed", error_code="X")
    assert settled.status == "failed"
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


def test_extract_skips_over_length_message(db_session):
    """超长消息不截断（截断会使原文校验失败），直接跳过且不影响 settle。"""
    from app import config

    text = "我妈妈生日是 3 月 5 号" + "啊" * (config.AGENT_MESSAGE_MAX_LENGTH + 100)
    user, _session, _message, run = _settled_run_with_text(db_session, text, "memex-toolong")
    assert run.status == "succeeded"
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


def test_extract_after_settle_propose_failure_swallowed(db_session, monkeypatch):
    user, space = create_agent_fixture(db_session, name="memex-swallow")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session, content={"text": "我妈妈生日是 3 月 5 号"})
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )

    from app.services import memory_extractor as me
    from app.services import memory_rag

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    # propose_candidate 炸掉也不会冒泡：settle 路径不受影响
    monkeypatch.setattr(memory_rag, "propose_candidate", _boom)
    assert me.extract_after_settle(db_session, run) == 0
    # savepoint 回滚后不留下部分写入
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


def test_settle_succeeds_even_if_extraction_raises(db_session, monkeypatch):
    """提取器整体抛错时 settle 必须照常成功（终态权威）。"""
    user, space = create_agent_fixture(db_session, name="memex-hookboom")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session, content={"text": "我妈妈生日是 3 月 5 号"})
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.id == run.id

    from app.services import memory_extractor as me

    def _boom(*args, **kwargs):
        raise RuntimeError("hook boom")

    monkeypatch.setattr(me, "extract_after_settle", _boom)
    settled = agent_queue.settle_run(db_session, run, status="succeeded")
    assert settled.status == "succeeded"


def test_settle_survives_deferred_flush_failure(db_session, monkeypatch):
    """延迟到外层 flush 的失败也不得击穿 settle（savepoint 隔离）。"""
    user, space = create_agent_fixture(db_session, name="memex-deferred")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    message = create_agent_message(db_session, session, content={"text": "我妈妈生日是 3 月 5 号"})
    run = agent_queue.enqueue_run(
        db_session,
        agent_session=session,
        kind="assistant",
        policy_version="test-policy-1",
        tool_allowlist=["familygraph.echo"],
        message=message,
    )
    grant = agent_queue.lease_next(db_session, kind="assistant", leased_by="sc")
    assert grant is not None and grant.run.id == run.id

    from app.models.agent import AgentMessage
    from app.services import memory_rag

    original = memory_rag.propose_candidate

    def _deferred_failure(*args, **kwargs):
        # 先真实落库，再注入一个会在 flush 时炸掉的 pending 对象
        row = original(*args, **kwargs)
        db_session.add(
            AgentMessage(
                session_id=message.session_id,
                role="assistant",
                content_json={"text": "deferred"},
                created_at=None,  # NOT NULL 违规，延迟到 flush 才暴露
            )
        )
        return row

    monkeypatch.setattr(memory_rag, "propose_candidate", _deferred_failure)
    settled = agent_queue.settle_run(db_session, run, status="succeeded")
    assert settled.status == "succeeded"
    db_session.expire_all()
    refreshed = db_session.get(type(run), run.id)
    assert refreshed is not None and refreshed.status == "succeeded"
    # 注入的坏对象与部分写入都不得留在库里
    rows = list(
        db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert rows == []


@pytest.mark.parametrize(
    "text,expected_summary",
    [
        ("爷爷生日是 12 月 1 号", "生日：12月1日"),
        ("外婆阴历生日是 6 月 20 日", "生日：6月20日（农历）"),
        ("婶婶不吃香菜", None),  # 「不吃」应命中 dietary
    ],
)
def test_detector_parametrized(db_session, text, expected_summary):
    items = rule_detector(text)
    if expected_summary is None:
        assert any(i.summary.startswith("饮食禁忌") for i in items)
    else:
        assert _summaries(items) == [expected_summary]


def test_extractor_seam_uses_item_message_id_for_key(db_session):
    """公共 seam：detector 自带 source_message_id 时键不得跨消息碰撞。"""
    from app.services.memory_rag import MemoryCandidateExtractor, MemoryCandidateInput

    user, space = create_agent_fixture(db_session, name="memex-seam")
    session = create_agent_session(db_session, account_id=user.account.id, space_id=space.id)
    m1 = create_agent_message(db_session, session, content={"text": "msg one"})
    m2 = create_agent_message(db_session, session, content={"text": "msg two"})

    def detector_for(message_id):
        return lambda text: [
            MemoryCandidateInput(
                source_quote=text,
                summary=f"偏好：{text}",
                suggested_scope="private",
                purpose="测试用途",
                sensitivity="normal",
                source_message_id=message_id,
                extractor_category="preference",
            )
        ]

    first = MemoryCandidateExtractor(detector=detector_for(m1.id))
    assert (
        len(
            first.extract(
                db_session, author_account_id=user.account.id, conversation_text="msg one"
            )
        )
        == 1
    )
    second = MemoryCandidateExtractor(detector=detector_for(m2.id))
    assert (
        len(
            second.extract(
                db_session, author_account_id=user.account.id, conversation_text="msg two"
            )
        )
        == 1
    )
    keys = sorted(
        row.idempotency_key
        for row in db_session.scalars(
            select(MemoryCandidate).where(MemoryCandidate.author_account_id == user.account.id)
        )
    )
    assert keys == [f"extract:{m1.id}:preference:0", f"extract:{m2.id}:preference:0"]
