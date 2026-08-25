"""同名同 PIN 消歧 challenge（AD-2）：落库 + 数据库保证单次使用防重放。

select 路径：单事务 UPDATE ... SET used_at WHERE jti=? AND used_at IS NULL AND
expires_at>now()，影响行数=0 即拒绝——过期与重放走同一拒绝路径。
"""

import json
import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import config
from app.models.auth_challenge import AuthChallenge
from app.utils import timeutil


def create_challenge(session: Session, candidate_ids: list[int], ip: str) -> AuthChallenge:
    """写入新 challenge 行；candidate 顺序即 409 响应的候选顺序。"""
    challenge = AuthChallenge(
        jti=secrets.token_urlsafe(32),
        candidate_ids_json=json.dumps(candidate_ids),
        ip=ip,
        expires_at=timeutil.utcnow() + timedelta(minutes=config.AUTH_CHALLENGE_TTL_MINUTES),
        used_at=None,
    )
    session.add(challenge)
    session.flush()  # 拿到 id/jti 供响应构造，不提交（由调用方事务控制）
    return challenge


def load_candidate_ids(session: Session, jti: str) -> list[int] | None:
    """按 jti 取候选集；行不存在返回 None。不做单次使用标记。"""
    challenge = session.query(AuthChallenge).filter(AuthChallenge.jti == jti).first()
    if challenge is None:
        return None
    ids: list[int] = json.loads(challenge.candidate_ids_json)
    return ids


def consume_challenge(session: Session, jti: str, ip: str, user_id: int) -> bool:
    """原子消费 challenge：单条 UPDATE 同时校验未用、未过期、IP 绑定。

    影响行数≠1 统一拒绝：过期/已用/重放/IP 不符走同一拒绝路径。其中 IP 不符
    不满足 WHERE 条件、不消耗 challenge（合法用户可重试，见测试
    test_ip_binding_rejected_without_consuming）；候选集外的 user_id 则在
    UPDATE 成功后拒绝——此时 challenge 已被烧掉，可疑提交 fail-closed。
    """
    now = timeutil.utcnow()
    result = session.execute(
        update(AuthChallenge)
        .where(
            AuthChallenge.jti == jti,
            AuthChallenge.used_at.is_(None),
            AuthChallenge.expires_at > now,
            AuthChallenge.ip == ip,
        )
        .values(used_at=now)
    )
    if result.rowcount != 1:
        return False
    challenge = session.query(AuthChallenge).filter(AuthChallenge.jti == jti).one()
    candidate_ids: list[int] = json.loads(challenge.candidate_ids_json)
    return user_id in candidate_ids
