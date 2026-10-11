"""U4：后台片段抽取 pass。

settle 路径只看**当轮那一条**用户消息，所以一条事实若分散在多条消息里就永远拼不出来
（「我妈住在杭州」+ 下一轮「是西湖区」）。这不是正则不够好，而是**观察窗口**太窄。

本文件钉住片段 pass 的四条性质：

1. 它读**一段**会话，因此能看到跨消息上下文；
2. 它只用确定性规则，产物仍是 **pending 候选**（确认门不变）；
3. 它是**幂等**的：重复跑同一窗口不产生重复候选，settle 已提过的同一事实也不再提；
4. 它只看**用户**消息 —— assistant 的回复可能是模型猜测，把它当事实来源等于让幻觉
   进入记忆候选。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models.agent import AgentMessage, AgentSession
from app.models.memory import MemoryCandidate
from app.models.platform_features import PlatformFeatureConfig
from app.services import memory_extractor
from app.utils.timeutil import utcnow
from conftest import create_agent_fixture


def _features(db) -> None:
    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(
            id=1, memory_enabled=True, rag_enabled=False, updated_at=utcnow()
        )
        db.add(row)
    else:
        row.memory_enabled = True
    db.flush()


def _session(db, user, space) -> AgentSession:
    session = AgentSession(
        account_id=user.account.id,
        space_id=space.id,
        agent_kind="assistant",
        term_usage_consent=False,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(session)
    db.flush()
    return session


def _say(db, session, text: str, *, role: str = "user", at=None) -> AgentMessage:
    message = AgentMessage(
        session_id=session.id,
        role=role,
        content_json={"text": text},
        created_at=at or utcnow(),
    )
    db.add(message)
    db.flush()
    return message


def test_segment_pass_sees_facts_the_settle_path_cannot(db_session):
    """跨消息上下文：单条都不构成一个事实，合起来才构成。"""
    user, space = create_agent_fixture(db_session, name="u4-segment")
    _features(db_session)
    session = _session(db_session, user, space)
    _say(db_session, session, "我妈住在杭州。")
    _say(db_session, session, "是西湖区。")
    db_session.commit()

    counters = memory_extractor.extract_session_segments(db_session)
    assert counters["sessions_scanned"] == 1
    candidates = db_session.scalars(select(MemoryCandidate)).all()
    # 片段里的「杭州」+「西湖区」由居住地规则抓到（单条消息抓不到「西湖区」这条）。
    assert any("杭州" in c.summary or "西湖" in c.summary for c in candidates), [
        c.summary for c in candidates
    ]
    # 产物必须是 pending —— 确认门不变。
    assert all(c.status == "pending" for c in candidates)


def test_segment_pass_is_idempotent(db_session):
    """重复跑同一窗口不产生重复候选。"""
    user, space = create_agent_fixture(db_session, name="u4-idem")
    _features(db_session)
    session = _session(db_session, user, space)
    _say(db_session, session, "我生日是三月五日。")
    db_session.commit()

    first = memory_extractor.extract_session_segments(db_session)
    assert first["candidates_proposed"] > 0
    total = len(db_session.scalars(select(MemoryCandidate)).all())

    second = memory_extractor.extract_session_segments(db_session)
    assert second["candidates_proposed"] == 0
    assert second["candidates_skipped"] > 0
    assert len(db_session.scalars(select(MemoryCandidate)).all()) == total


def test_segment_pass_does_not_duplicate_what_settle_already_proposed(db_session):
    """settle 已提过的同一事实，片段 pass 不再提一遍。

    去重看的是**事实本身**（同账号同 summary），不是「从哪条消息来的」——否则
    同一句话经过两条路径就会产生两张待确认卡。
    """
    from app.services.memory_rag import MemoryCandidateExtractor

    user, space = create_agent_fixture(db_session, name="u4-dedup")
    _features(db_session)
    session = _session(db_session, user, space)
    message = _say(db_session, session, "我生日是三月五日。")
    db_session.commit()

    inputs, _dropped = memory_extractor.rule_detector_with_stats("我生日是三月五日。")
    assert inputs, "前提：这条消息 settle 路径会提出候选"
    extractor = MemoryCandidateExtractor(detector=lambda _t: inputs)
    extractor.version = memory_extractor.EXTRACTOR_VERSION
    extractor.extract(
        db_session,
        author_account_id=user.account.id,
        conversation_text="我生日是三月五日。",
        source_message_id=message.id,
    )
    db_session.commit()
    before = len(db_session.scalars(select(MemoryCandidate)).all())

    counters = memory_extractor.extract_session_segments(db_session)
    assert counters["candidates_proposed"] == 0
    assert len(db_session.scalars(select(MemoryCandidate)).all()) == before


def test_segment_pass_ignores_assistant_messages(db_session):
    """assistant 的回复不是事实来源：它可能是模型猜测。"""
    user, space = create_agent_fixture(db_session, name="u4-role")
    _features(db_session)
    session = _session(db_session, user, space)
    _say(db_session, session, "我生日是三月五日。", role="assistant")
    db_session.commit()

    counters = memory_extractor.extract_session_segments(db_session)
    assert counters["candidates_proposed"] == 0
    assert db_session.scalars(select(MemoryCandidate)).all() == []


def test_segment_pass_skips_stale_sessions(db_session):
    """只处理最近活跃过的会话（工作量上界，不是正确性依赖）。"""
    user, space = create_agent_fixture(db_session, name="u4-stale")
    _features(db_session)
    session = _session(db_session, user, space)
    stale = utcnow() - timedelta(minutes=memory_extractor.SEGMENT_ACTIVITY_MINUTES + 60)
    _say(db_session, session, "我生日是三月五日。", at=stale)
    db_session.commit()

    counters = memory_extractor.extract_session_segments(db_session)
    assert counters["sessions_scanned"] == 0
    assert db_session.scalars(select(MemoryCandidate)).all() == []


def test_segment_pass_is_bounded_and_never_raises(db_session):
    """后台 pass 不得因单个会话失败而中断，且计数键稳定。"""
    user, space = create_agent_fixture(db_session, name="u4-bounded")
    _features(db_session)
    session = _session(db_session, user, space)
    for index in range(30):
        _say(db_session, session, f"第 {index} 句闲聊，没有任何事实。")
    db_session.commit()

    counters = memory_extractor.extract_session_segments(db_session, limit=1)
    assert set(counters) == {"sessions_scanned", "candidates_proposed", "candidates_skipped"}
    assert counters["sessions_scanned"] <= 1
