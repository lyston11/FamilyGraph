"""成员档案命令（建档/档案修改/披露开关/删除/重复合并）——HTTP 与未来 Agent 共用（AC-F7）。

每条命令一个短事务：授权（custody/space_fsm）→ 校验 → 写入 → domain_events → audit。
建档（F-1/F-3）：provisional 档案 + managed 账号；选空间只建 space_profile_refs
最小节点引用，provisional 人物不是 SpaceMember。确档清单项随建档播种。
合并（merge_duplicate_profile）：残留重复档案的显式处置命令——把 retired 的身份
承载行改指向 survivor 后删除 retired（§0.9 判定口径 + R2 处置闭环）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.commands.context import ActorContext, command_transaction, load_actor
from app.errors import (
    AUTH_INVALID_CREDENTIALS,
    CONFIRM_NAME_MISMATCH,
    DISCLOSURE_SCOPE_REQUIRES_SELF,
    IDEMPOTENCY_PAYLOAD_CONFLICT,
    IDENTITY_INVALID_TRANSITION,
    OWNER_TRANSFER_REQUIRED,
    PERSON_DUPLICATE_AMBIGUOUS,
    PERSON_DUPLICATE_IN_SPACE,
    SPACE_NOT_FOUND,
    UNIFIED_CREDENTIAL_MESSAGE,
    USER_NOT_FOUND,
    VALIDATION_ERROR,
    raise_api_error,
)
from app.models import Account, User
from app.models.account_binding import AccountBinding
from app.models.attachment import Attachment
from app.models.relation import Relation
from app.models.space import FamilySpace, SpaceMember, SpaceProfileRef
from app.models.v2_foundation import MemberCreationRequest, ProfileFactReview
from app.services import audit, custody, identity_fsm, person_identity, relation_fsm, source_facts
from app.services import disclosure as disclosure_service
from app.services.domain_events import emit
from app.utils import security, timeutil


@dataclass
class DeletedProfile:
    """删除命令结果：物理文件清理在事务提交后由调用方执行（外部 I/O 不进事务）。"""

    profile_id: int
    snapshot: dict[str, Any]
    purge_image_paths: list[str] = field(default_factory=list)


@dataclass
class MergedProfile:
    """合并命令结果：already_merged=True 表示 retired 已不存在（重放幂等分支）。"""

    survivor_id: int
    retired_id: int
    moved: dict[str, int] = field(default_factory=dict)
    already_merged: bool = False


@dataclass(frozen=True)
class ManagedMemberOutcome:
    """建档命令结果（POST /users 命令层）。

    - 新建 managed：pin 为一次性明文 PIN，仅本次响应可见；
    - 撞名转绑定（决策 16）：不建 managed 账号，pin=None，binding_id 指向新建的
      pending 绑定请求（响应以 bound_to_existing 标记，不含被绑定方任何数据）；
    - 幂等重放：replayed=True，不回放初始 PIN。
    """

    member: User
    pin: str | None
    replayed: bool
    binding_id: int | None = None

    @property
    def bound_to_existing(self) -> bool:
        return self.binding_id is not None


def _enrich(value: Any) -> Any:
    from app.services.lunar import enrich_structured_date

    return enrich_structured_date(value) if isinstance(value, dict) else value


def _seed_fact_reviews(session: Session, member: User) -> None:
    """确档清单播种（F-1）：名字必审，其余按已填字段生成；创建者关系单独一项。

    自由描述不自动成为正式事实 —— 清单项只是「待本人核对」的提议。
    """
    now = timeutil.utcnow()
    items: list[tuple[str, dict[str, Any]]] = [("name", {"field": "name", "value": member.name})]
    for field_name in ("gender", "birth", "death", "bio"):
        value = getattr(member, field_name)
        if value not in (None, "") and value != "unknown":
            items.append((field_name, {"field": field_name}))
    if member.created_by is not None:
        creator = session.get(User, member.created_by)
        items.append(
            (
                "relation_to_creator",
                {
                    "creator_id": member.created_by,
                    "creator_name": creator.name if creator else None,
                },
            )
        )
    for item_type, ref in items:
        session.add(
            ProfileFactReview(
                profile_id=member.id,
                item_type=item_type,
                item_ref_json=ref,
                proposed_by=member.created_by,
                status="proposed",
                created_at=now,
            )
        )


def _guard_duplicate_person(
    session: Session,
    *,
    space_id: int,
    name: str,
    birth: dict[str, Any] | None,
    allow_duplicate_person: bool,
) -> None:
    """同一空间不得出现同一人的两份档案（判定口径见 services/person_identity）。

    重复建档等于多出一份可登录凭据（每个 User 携带一个 Account 与一次性 PIN），
    所以强匹配一律拒绝，并在 detail 里给出既有档案 id——调用方应改为引用它
    （加 SpaceProfileRef）而不是新建。

    并发保证由调用方的立即事务提供：写锁前置后，检查与插入之间无竞态窗口。
    """
    candidates = person_identity.find_duplicate_candidates(
        session, space_id=space_id, name=name, birth=birth
    )
    if not candidates:
        return
    strong = [c for c in candidates if c.strength == person_identity.STRENGTH_STRONG]
    if strong:
        raise_api_error(
            409,
            PERSON_DUPLICATE_IN_SPACE,
            "该空间已存在同一个人的档案，请引用现有档案而不是新建",
            detail={
                "space_id": space_id,
                "existing": [{"user_id": c.user_id, "name": c.name} for c in strong],
                "resolution": "reference_existing",
            },
        )
    if not allow_duplicate_person:
        raise_api_error(
            409,
            PERSON_DUPLICATE_AMBIGUOUS,
            "该空间已有同名档案且生日缺失，无法判定是否同一人，请确认",
            detail={
                "space_id": space_id,
                "candidates": [
                    {"user_id": c.user_id, "name": c.name, "birth_known": c.birth_key is not None}
                    for c in candidates
                ],
                "resolution": "reference_existing_or_confirm_distinct",
            },
        )


def _account_status_of(session: Session, user_id: int) -> str | None:
    return session.scalar(select(Account.status).where(Account.user_id == user_id))


def _is_self_registered_claimed(session: Session, user_id: int) -> bool:
    """自注册账号判定（决策 16 绑定分支的目标资格）。

    自助注册（commands/registration.py）的初始态 = Account claimed 且
    ``users.created_by IS NULL``；建档产生的账号恒有代管人（created_by 非空）。
    无 Account 的行（绑定中 provisional 人物）返回 False，维持既有 409 门禁。
    创建者被删除后 created_by 置 NULL 的 claimed 档案同样视为可绑定对象——
    它已是本人持有的真实凭据，「本人确认后并回」的语义仍然成立。
    """
    if _account_status_of(session, user_id) != identity_fsm.ACCOUNT_CLAIMED:
        return False
    return session.scalar(select(User.created_by).where(User.id == user_id)) is None


def _find_self_registered_binding_target(
    session: Session,
    *,
    space_id: int,
    name: str,
    birth: dict[str, Any] | None,
    actor_id: int,
    allow_duplicate_person: bool,
) -> person_identity.DuplicateCandidate | None:
    """建档撞名并流判定（决策 16）：查重命中 claimed 自注册账号 → 转绑定请求。

    判定口径与既有门禁 ``_guard_duplicate_person`` 同源（person_identity 单点）：

    - 存在强匹配候选且**并非全部**为自注册 claimed → None（既有 409 门禁原样生效，
      managed 重复档案仍被硬拦）；
    - 强匹配候选全部为自注册 claimed → 返回首个候选（``allow_duplicate_person``
      不放宽强匹配，与 §0.9 一致）；
    - 弱匹配：创建者已显式消歧（allow=True）→ None（既有放行路径）；
      未消歧且存在自注册 claimed 弱候选 → 返回首个候选——是否同一人交由本人
      确认/拒绝裁决，确认前不向发起人泄露该候选任何数据。

    只在建档命令选空间时调用（查重门禁本就是空间作用域）；无空间建档维持现状。
    """
    candidates = person_identity.find_duplicate_candidates(
        session, space_id=space_id, name=name, birth=birth, exclude_user_id=actor_id
    )
    if not candidates:
        return None
    strong = [c for c in candidates if c.strength == person_identity.STRENGTH_STRONG]
    if strong:
        if all(_is_self_registered_claimed(session, c.user_id) for c in strong):
            return strong[0]
        return None
    if allow_duplicate_person:
        return None
    weak_self_registered = [
        c
        for c in candidates
        if c.strength == person_identity.STRENGTH_WEAK
        and _is_self_registered_claimed(session, c.user_id)
    ]
    return weak_self_registered[0] if weak_self_registered else None


def _create_binding_person(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    gender: str,
    birth: dict[str, Any] | None,
    death: dict[str, Any] | None,
    bio: str | None,
    privacy_mode: str,
    space: FamilySpace | None,
) -> User:
    """撞名绑定分支的 provisional 人物：**不建 managed 账号**（决策 16 / design §4）。

    人物行承载建档录入的数据，但不配发 Account/PIN——它注定并回被绑定人的既有
    user，提前配发只会多出一份可登录凭据（§0.9：重复建档 = 多出一份凭据）。
    无凭据人物不参与登录（登录 join accounts 天然不可见），确认前仅占空间人物
    身份槽位（SpaceProfileRef）。不做重复门禁：调用方已裁决走绑定分支。
    """
    now = timeutil.utcnow()
    person = User(
        name=name.strip(),
        created_at=now,
        gender=gender,
        birth=_enrich(birth),
        death=_enrich(death),
        bio=bio,
        privacy_mode=privacy_mode,
        created_by=ctx.user_id,
        profile_status="provisional",
    )
    session.add(person)
    session.flush()  # 取得 id 供空间引用/绑定行/关系边引用
    if space is not None:
        session.add(
            SpaceProfileRef(
                space_id=space.id,
                user_id=person.id,
                added_by=ctx.user_id,
                status="active",
                created_at=now,
            )
        )
    emit(
        session,
        event_type="profile.created",
        aggregate_type="profile",
        aggregate_id=person.id,
        payload={"name": person.name, "created_by": ctx.user_id, "binding_pending": True},
        space_id=space.id if space is not None else None,
        actor_account_id=ctx.account_id,
    )
    audit.write_audit(
        session,
        action="profile_created",
        actor_id=ctx.user_id,
        target_id=person.id,
        ip=ctx.ip,
        detail={"name": person.name, "privacy_mode": privacy_mode, "binding_pending": True},
    )
    return person


def _create_member_core(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
    allow_duplicate_person: bool = False,
) -> tuple[User, str]:
    """建房核心（F-1/F-3）：provisional 档案 + managed 账号 + 一次性 PIN + 空间引用
    + 确档清单。不管理事务，由调用方包在自己的 command_transaction 内。

    ``allow_duplicate_person`` 只放宽**弱**匹配（同名但生日缺失，不可判定）：
    创建者显式确认"这是另一个人"后放行。强匹配（同名同生日）不受此开关影响，
    始终拒绝——那不是需要人来消歧的情况。
    """
    actor = load_actor(session, ctx)
    now = timeutil.utcnow()
    # 空间校验前置于建行：去重门禁必须在 User/Account 落库之前判定。
    space: FamilySpace | None = None
    if space_membership_space_id is not None:
        space = session.get(FamilySpace, space_membership_space_id)
        if space is None or not space_fsm_is_active(session, space.id, actor.id):
            raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
        _guard_duplicate_person(
            session,
            space_id=space.id,
            name=name,
            birth=birth,
            allow_duplicate_person=allow_duplicate_person,
        )
    pin = security.generate_pin()
    member = User(
        name=name.strip(),
        created_at=now,
        gender=gender,
        birth=_enrich(birth),
        death=_enrich(death),
        bio=bio,
        privacy_mode=privacy_mode,
        created_by=actor.id,
        # F-3：新建他人恒为 provisional 档案；身份确认由本人完成
        profile_status="provisional",
    )
    member.account = Account(
        pin_hash=security.hash_pin(pin),
        pin_must_change=True,
        token_version=0,
        failed_attempts=0,
        locked_until=None,
        status="managed",
    )
    session.add(member)
    session.flush()  # 取得 id 供空间引用/清单/审计引用

    space_id: int | None = None
    if space is not None:
        space_id = space.id
        session.add(
            SpaceProfileRef(
                space_id=space.id,
                user_id=member.id,
                added_by=actor.id,
                status="active",
                created_at=now,
            )
        )

    _seed_fact_reviews(session, member)
    emit(
        session,
        event_type="profile.created",
        aggregate_type="profile",
        aggregate_id=member.id,
        payload={"name": member.name, "created_by": actor.id},
        space_id=space_id,
        actor_account_id=ctx.account_id,
    )
    audit.write_audit(
        session,
        action="profile_created",
        actor_id=actor.id,
        target_id=member.id,
        ip=ctx.ip,
        detail={"name": member.name, "privacy_mode": privacy_mode},
    )
    return member, pin


def create_member(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
    allow_duplicate_person: bool = False,
) -> tuple[User, str]:
    """低层建房（内部/测试用）：仅 user+account+PIN，无关系。

    公开「名字+关系必填」语义由 create_managed_member（POST /users）强制。
    返回 (member, 明文 PIN)；PIN 仅本次响应可见（A3/AD-1）。
    """
    with command_transaction(session, immediate=True):
        return _create_member_core(
            session,
            ctx,
            name=name,
            gender=gender,
            birth=birth,
            death=death,
            bio=bio,
            privacy_mode=privacy_mode,
            space_membership_space_id=space_membership_space_id,
            allow_duplicate_person=allow_duplicate_person,
        )


def create_managed_member(
    session: Session,
    ctx: ActorContext,
    *,
    name: str,
    relation_dir_class: str,
    idempotency_key: str,
    request_hash: str,
    gender: str = "unknown",
    birth: dict[str, Any] | None = None,
    death: dict[str, Any] | None = None,
    bio: str | None = None,
    privacy_mode: str = "handover",
    space_membership_space_id: int | None = None,
    relation_label: str | None = None,
    relation_text: str | None = None,
    allow_duplicate_person: bool = False,
) -> ManagedMemberOutcome:
    """F-1 原子建档：provisional 档案 + managed 账号 +（AD-4 新建例外）直接 active
    关系 + 关系原文 + proposed SourceFact + 空间引用 + 事件/审计 + 幂等台账，
    任一步失败整体回滚。

    撞名并流（决策 16 / design §4）：目标空间查重命中已存在的 claimed 自注册账号
    时**不建 managed 账号**，改创建 account_bindings(pending)——建档录入成为无凭据
    provisional 人物，关系边保持 pending（目标为已存在账号不走 AD-4 新建例外），
    被绑定人本人以 PIN + 「这是我」确认后并回（commands/bindings.py）。
    发起人响应只见 bound_to_existing 标记，全程不泄露被绑定方任何数据。

    返回 ManagedMemberOutcome：新建时 pin 为一次性 PIN（仅本次响应可见）；同幂等键
    重放返回原档案但不回放 PIN（replayed=True, pin=None）。
    """
    actor = load_actor(session, ctx)
    key = idempotency_key.strip()
    if not key:
        raise_api_error(422, VALIDATION_ERROR, "缺少幂等请求键")
    if len(key) > 120:
        raise_api_error(422, VALIDATION_ERROR, "幂等请求键过长")

    prior = _find_member_creation(session, actor.id, key)
    if prior is not None:
        return _replay_member_creation(session, prior, request_hash)

    try:
        with command_transaction(session, immediate=True):
            # ---- 撞名并流判定（决策 16）：先于任何建档写入，与门禁同一立即事务 ----
            binding_space: FamilySpace | None = None
            binding_candidate: person_identity.DuplicateCandidate | None = None
            if space_membership_space_id is not None:
                binding_space = session.get(FamilySpace, space_membership_space_id)
                if binding_space is None or not space_fsm_is_active(
                    session, binding_space.id, actor.id
                ):
                    raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在或无权操作")
                binding_candidate = _find_self_registered_binding_target(
                    session,
                    space_id=binding_space.id,
                    name=name,
                    birth=birth,
                    actor_id=actor.id,
                    allow_duplicate_person=allow_duplicate_person,
                )

            if binding_candidate is not None and binding_space is not None:
                binding_target = session.get(User, binding_candidate.user_id)
                if binding_target is None:  # 理论不可达：候选来自真实行
                    raise_api_error(409, PERSON_DUPLICATE_IN_SPACE, "该空间已存在同一个人的档案")
                person = _create_binding_person(
                    session,
                    ctx,
                    name=name,
                    gender=gender,
                    birth=birth,
                    death=death,
                    bio=bio,
                    privacy_mode=privacy_mode,
                    space=binding_space,
                )
                # 目标为已存在/claimed 账号 → pending 合并确认流（AD-4）；
                # 确认时随人物并回改指向 target 后由本人 accept（commands/bindings.py）
                edge = relation_fsm.create_relation(
                    session,
                    from_user=actor.id,
                    to_user=person.id,
                    dir_class=relation_dir_class,
                    label=relation_label,
                    status="pending",
                )
                emit(
                    session,
                    event_type="relation.created",
                    aggregate_type="relation",
                    aggregate_id=edge.id,
                    payload={
                        "from_user": edge.from_user,
                        "to_user": edge.to_user,
                        "dir_class": edge.dir_class,
                        "status": edge.status,
                    },
                    actor_account_id=ctx.account_id,
                )
                # 关系原文 append-only（有则保存）；proposed SourceFact 随确认并回改指向
                binding_raw_text_id: int | None = None
                if relation_text is not None and relation_text.strip():
                    raw = source_facts.create_raw_relation_input(
                        session,
                        author_account_id=ctx.account_id,
                        text=relation_text,
                        context={"dir_class": relation_dir_class, "label": relation_label},
                    )
                    binding_raw_text_id = raw.id
                source_facts.create_structural_edge_proposal(
                    session,
                    from_user=actor.id,
                    to_user=person.id,
                    dir_class=relation_dir_class,
                    raw_text_id=binding_raw_text_id,
                    asserted_by_account_id=ctx.account_id,
                )
                binding = AccountBinding(
                    initiator_id=actor.id,
                    target_id=binding_target.id,
                    person_id=person.id,
                    status="pending",
                    created_at=timeutil.utcnow(),
                )
                session.add(binding)
                session.flush()
                audit.write_audit(
                    session,
                    action="account_binding_created",
                    actor_id=actor.id,
                    target_id=binding.id,
                    ip=ctx.ip,
                    detail={
                        "person_id": person.id,
                        "target_id": binding_target.id,
                        "space_id": binding_space.id,
                        "match_strength": binding_candidate.strength,
                    },
                )
                session.add(
                    MemberCreationRequest(
                        actor_user_id=actor.id,
                        idempotency_key=key,
                        request_hash=request_hash,
                        member_user_id=person.id,
                        relation_id=edge.id,
                        created_at=timeutil.utcnow(),
                    )
                )
                return ManagedMemberOutcome(
                    member=person, pin=None, replayed=False, binding_id=binding.id
                )

            member, pin = _create_member_core(
                session,
                ctx,
                name=name,
                gender=gender,
                birth=birth,
                death=death,
                bio=bio,
                privacy_mode=privacy_mode,
                space_membership_space_id=space_membership_space_id,
                allow_duplicate_person=allow_duplicate_person,
            )

            # AD-4 新建账号例外：managed 新档由代管人创建 → relation 直接 active
            edge = relation_fsm.create_relation(
                session,
                from_user=actor.id,
                to_user=member.id,
                dir_class=relation_dir_class,
                label=relation_label,
                status="active",
            )

            # 关系原文 append-only（有则保存）；无原文时以 dir_class+label 组成上下文
            raw_text_id: int | None = None
            if relation_text is not None and relation_text.strip():
                raw = source_facts.create_raw_relation_input(
                    session,
                    author_account_id=ctx.account_id,
                    text=relation_text,
                    context={"dir_class": relation_dir_class, "label": relation_label},
                )
                raw_text_id = raw.id
            # proposed SourceFact（关系仍待对方确档确认；结构真源不由写入者单方决定）
            source_facts.create_structural_edge_proposal(
                session,
                from_user=actor.id,
                to_user=member.id,
                dir_class=relation_dir_class,
                raw_text_id=raw_text_id,
                asserted_by_account_id=ctx.account_id,
            )

            emit(
                session,
                event_type="relation.created",
                aggregate_type="relation",
                aggregate_id=edge.id,
                payload={
                    "from_user": edge.from_user,
                    "to_user": edge.to_user,
                    "dir_class": edge.dir_class,
                    "status": edge.status,
                },
                actor_account_id=ctx.account_id,
            )
            audit.write_audit(
                session,
                action="relation_created",
                actor_id=actor.id,
                target_id=member.id,
                ip=ctx.ip,
                detail={"relation_id": edge.id, "dir_class": relation_dir_class},
            )

            session.add(
                MemberCreationRequest(
                    actor_user_id=actor.id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    member_user_id=member.id,
                    relation_id=edge.id,
                    created_at=timeutil.utcnow(),
                )
            )
            return ManagedMemberOutcome(member=member, pin=pin, replayed=False)
    except IntegrityError:
        # 并发窗口：同 key 由另一请求先提交 → 唯一约束冲突，按重放裁决
        session.rollback()
        prior = _find_member_creation(session, actor.id, key)
        if prior is not None:
            return _replay_member_creation(session, prior, request_hash)
        raise


def _find_member_creation(
    session: Session, actor_user_id: int, key: str
) -> MemberCreationRequest | None:
    return session.scalar(
        select(MemberCreationRequest).where(
            MemberCreationRequest.actor_user_id == actor_user_id,
            MemberCreationRequest.idempotency_key == key,
        )
    )


def _replay_member_creation(
    session: Session, prior: MemberCreationRequest, request_hash: str
) -> ManagedMemberOutcome:
    if prior.request_hash != request_hash:
        raise_api_error(409, IDEMPOTENCY_PAYLOAD_CONFLICT, "相同请求键但请求内容不同")
    member = session.get(User, prior.member_user_id)
    if member is None:
        raise_api_error(409, VALIDATION_ERROR, "幂等请求对应的档案已不存在")
    # 撞名绑定建档重放：找回原绑定行以保持 bound_to_existing 标记（绑定行终态保留）
    binding_id = session.scalar(
        select(AccountBinding.id)
        .where(AccountBinding.person_id == member.id)
        .order_by(AccountBinding.id.desc())
        .limit(1)
    )
    return ManagedMemberOutcome(member=member, pin=None, replayed=True, binding_id=binding_id)


def canonical_member_request_hash(
    *,
    name: str,
    gender: str,
    birth: dict[str, Any] | None,
    death: dict[str, Any] | None,
    bio: str | None,
    privacy_mode: str,
    space_membership_space_id: int | None,
    relation_dir_class: str,
    relation_label: str | None,
    relation_text: str | None,
) -> str:
    """请求内容指纹：稳定字段序 + key 排序；同请求键重放时据此判定内容一致。"""
    canonical = {
        "name": name.strip(),
        "gender": gender,
        "birth": birth,
        "death": death,
        "bio": bio,
        "privacy_mode": privacy_mode,
        "space_membership_space_id": space_membership_space_id,
        "relation_dir_class": relation_dir_class,
        "relation_label": relation_label,
        "relation_text": relation_text.strip() if relation_text else None,
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def space_fsm_is_active(session: Session, space_id: int, user_id: int) -> bool:
    """局部转发避免模块命名冲突（services.space_fsm.is_active_member）。"""
    from app.services import space_fsm

    return space_fsm.is_active_member(session, space_id, user_id)


def rename_own_profile(session: Session, ctx: ActorContext, *, name: str) -> User:
    """本人改名（A1）：随时可改，不改名不失效会话。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        old_name = actor.name
        actor.name = name.strip()
        emit(
            session,
            event_type="profile.updated",
            aggregate_type="profile",
            aggregate_id=actor.id,
            payload={"fields": ["name"], "updated_by": actor.id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="name_changed",
            actor_id=actor.id,
            target_id=actor.id,
            ip=ctx.ip,
            detail={"old_name": old_name},
        )
    return actor


def change_own_pin(
    session: Session,
    ctx: ActorContext,
    *,
    old_pin: str,
    new_pin: str,
) -> tuple[User, bool]:
    """改 PIN：验旧 PIN → 新哈希 → 版本+1；首登强制改完成时触发认领转换。

    返回 (user, was_forced)；全部旧 refresh 会话同事务作废。
    """
    from app.services import refresh_session as refresh_session_service

    actor = load_actor(session, ctx)
    if not security.verify_pin(old_pin, actor.account.pin_hash):
        # 旧 PIN 错误同样走防枚举统一文案
        raise_api_error(401, AUTH_INVALID_CREDENTIALS, UNIFIED_CREDENTIAL_MESSAGE)

    was_forced = bool(actor.account.pin_must_change)
    with command_transaction(session):
        actor.account.pin_hash = security.hash_pin(new_pin)
        actor.account.pin_must_change = False
        actor.account.token_version += 1
        actor.account.failed_attempts = 0
        actor.account.locked_until = None
        if was_forced and actor.account.status == "managed":
            # 首登强制改 PIN 完成 = 认领完成（v2：managed→claimed 唯一转换点）
            identity_fsm.claim_account(session, actor.account)
            emit(
                session,
                event_type="account.claimed",
                aggregate_type="account",
                aggregate_id=actor.account.id,
                payload={"user_id": actor.id, "via": "pin_change"},
                actor_account_id=ctx.account_id,
            )
        refresh_session_service.revoke_all_active(session, actor.id, ip=None, reason="pin_change")
        audit.write_audit(
            session,
            action="pin_changed",
            actor_id=actor.id,
            target_id=actor.id,
            ip=ctx.ip,
            detail={"claim_completed": was_forced},
        )
    return actor, was_forced


def update_member_profile(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    changes: dict[str, Any],
) -> User:
    """档案字段编辑：custody 授权单点，变更审计 + 领域事件。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_edit(actor, target)

        applied: list[str] = []
        for field_name in ("gender", "birth", "death", "bio"):
            if field_name in changes:
                setattr(target, field_name, _enrich(changes[field_name]))
                applied.append(field_name)
        if changes.get("name") is not None:
            target.name = str(changes["name"]).strip()
            applied.append("name")

        emit(
            session,
            event_type="profile.updated",
            aggregate_type="profile",
            aggregate_id=target.id,
            payload={"fields": sorted(applied), "updated_by": actor.id},
            actor_account_id=ctx.account_id,
        )
        audit.write_audit(
            session,
            action="profile_updated",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail={"fields": sorted(applied)},
        )
    return target


def update_disclosure(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    flags: dict[str, bool],
    *,
    space_id: int | None = None,
) -> User:
    """披露开关整体替换（基础五类 + 高敏感五类）；修改权：全局=档案编辑权主体，
    逐空间覆盖（space_id 非空）仅档案本人（v2 Gap3：防止代管人代设空间披露）。

    09-05 高敏感策略放开：health/address/school/contact/private_notes 由本人显式
    开启（未成年人开启请求在服务层整体 422）；命中高敏感且值变化时追加
    disclosure_high_risk_changed 审计行（detail 只含类别名与目标值，无内容文本）。
    """
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_edit(actor, target)

        scope_payload: dict[str, Any] = {"disclosure": flags, "updated_by": actor.id}
        if space_id is not None:
            if actor.id != target.id:
                raise_api_error(
                    403, DISCLOSURE_SCOPE_REQUIRES_SELF, "逐空间披露偏好仅档案本人可修改"
                )
            space = session.get(FamilySpace, space_id)
            if space is None:
                raise_api_error(404, SPACE_NOT_FOUND, "目标家庭空间不存在")
            changed_high_risk = disclosure_service.set_space_disclosure(
                session, target, space_id, flags
            )
            scope_payload.update(scope="space", space_id=space_id)
        else:
            changed_high_risk = disclosure_service.set_basic_disclosure(session, target, flags)
            scope_payload["scope"] = "global"
        emit(
            session,
            event_type="disclosure.updated",
            aggregate_type="profile",
            aggregate_id=target.id,
            payload=scope_payload,
            actor_account_id=ctx.account_id,
            space_id=space_id,
        )
        audit.write_audit(
            session,
            action="disclosure_updated",
            actor_id=actor.id,
            target_id=target.id,
            ip=ctx.ip,
            detail=dict(scope_payload),
        )
        if changed_high_risk:
            audit.write_audit(
                session,
                action="disclosure_high_risk_changed",
                actor_id=actor.id,
                target_id=target.id,
                ip=ctx.ip,
                detail={
                    "scope": scope_payload["scope"],
                    **({"space_id": space_id} if scope_payload["scope"] == "space" else {}),
                    "categories": sorted(changed_high_risk),
                    "allowed": {category: bool(flags[category]) for category in changed_high_risk},
                },
            )
    return target


def delete_profile_core(
    session: Session,
    ctx: ActorContext,
    target: User,
    *,
    confirm_name: str,
) -> DeletedProfile:
    """删除核心（delete_member 与数据权利 execute-delete 共用）。

    单事务级联（账号/会话随 FK CASCADE）；空间所有者先经显式义务预检引导移交，
    owner_id RESTRICT 作为数据库兜底；audit 保留快照；tombstone 失效事件驱动
    缓存/附件/披露投影清理（§0.6 合同）。调用方负责在其自身 command_transaction
    内调用并在提交后清理物理文件。
    """
    from app.commands.ownership import assert_no_owner_obligations

    assert_no_owner_obligations(session, ctx, target.id)

    if confirm_name.strip() != target.name.strip():
        raise_api_error(409, CONFIRM_NAME_MISMATCH, "输入的名字与档案名字不一致")

    # 邀请码处置（09-05 P2-2）：码是临时分享凭据，删除前自动撤销未撤销码并写
    # audit（invite_code_auto_revoked_on_delete）；已核销/已撤销码不动，码行随
    # 0032 creator_id SET NULL 保留使用计数/撤销历史，删除不再被 FK RESTRICT 阻断。
    # 自删场景 actor 行随后级联消失，审计以快照留痕（actor_id 置 NULL，同 profile_deleted）。
    from app.services import invite_codes as invite_codes_service

    invite_codes_service.auto_revoke_for_creator_delete(
        session,
        creator_id=target.id,
        actor_id=None if ctx.user_id == target.id else ctx.user_id,
        ip=ctx.ip,
    )

    image_paths = [
        row.url_or_path
        for row in session.query(Attachment)
        .filter(Attachment.user_id == target.id, Attachment.type == "image")
        .all()
    ]
    snapshot = {
        "id": target.id,
        "name": target.name,
        "gender": target.gender,
        "birth": target.birth,
        "death": target.death,
        "bio": target.bio,
        "privacy_mode": target.privacy_mode,
        "profile_status": target.profile_status,
        # 撞名绑定中的 provisional 人物无 Account（决策 16）：快照记 None
        "account_status": target.account.status if target.account is not None else None,
        "created_by": target.created_by,
    }
    # profile.deleted 的失效合同需要空间范围：refs/members 行随删除级联消失，
    # 必须在删除前收集 active 空间写入 payload.space_ids（domain_events 监听逐空间标 stale）。
    affected_space_ids = sorted(
        {
            *session.scalars(
                select(SpaceProfileRef.space_id).where(
                    SpaceProfileRef.user_id == target.id,
                    SpaceProfileRef.status == "active",
                )
            ),
            *session.scalars(
                select(SpaceMember.space_id).where(
                    SpaceMember.user_id == target.id,
                    SpaceMember.status == "active",
                )
            ),
        }
    )
    # Publish before the profile is flushed away so the RAG invalidation can
    # still find documents owned by this profile. The event payload is a
    # deletion-safe snapshot and does not use a foreign key to the user.
    emit(
        session,
        event_type="profile.deleted",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={
            "snapshot_name": snapshot["name"],
            "deleted_by": ctx.user_id,
            "deleted_by_account": ctx.account_id,
            "space_ids": affected_space_ids,
        },
    )
    session.delete(target)  # flush 时级联删除账号等子行，audit 行保留（无 FK）
    try:
        session.flush()
    except Exception:
        # v2 §0.5 兜底：义务预检与 RESTRICT 之间的竞态窗口
        session.rollback()
        raise_api_error(
            409,
            OWNER_TRANSFER_REQUIRED,
            "该档案是家庭空间所有者，请先完成 owner 移交后再删除",
        )

    # The deletion event was emitted before the cascade so RAG tombstones are
    # durable even when the owner_user_id foreign key is set NULL by SQLite.
    emit(
        session,
        event_type="attachments.invalidated",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={"attachment_count": len(image_paths)},
    )
    emit(
        session,
        event_type="disclosure.invalidated",
        aggregate_type="profile",
        aggregate_id=target.id,
        payload={},
    )
    return DeletedProfile(profile_id=target.id, snapshot=snapshot, purge_image_paths=image_paths)


def delete_member(
    session: Session,
    ctx: ActorContext,
    target_id: int,
    *,
    confirm_name: str,
) -> DeletedProfile:
    """删除档案：本人 ∨ 代管创建者（custody 判定）；二次确认名字。"""
    actor = load_actor(session, ctx)
    with command_transaction(session):
        target = session.get(User, target_id)
        if target is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_delete(actor, target)

        result = delete_profile_core(session, ctx, target, confirm_name=confirm_name)
        audit.write_audit(
            session,
            action="profile_deleted",
            # 自删场景：actor 行已随级联删除，审计以快照留痕（actor_id 置 NULL）
            actor_id=None if actor.id == result.profile_id else actor.id,
            target_id=result.profile_id,
            ip=ctx.ip,
            detail={
                "snapshot": result.snapshot,
                "actor_id": actor.id,
                "self_deleted": actor.id == result.profile_id,
            },
        )
    return result


# ---- 残留重复档案的合并处置（09-01 人物身份去重，design §2.1）----


def _active_identity_space_ids(session: Session, *user_ids: int) -> list[int]:
    """两侧 active refs/members 覆盖的空间集合（profile.* 失效与审计的触达范围）。"""
    ids: set[int] = set()
    for user_id in user_ids:
        ids.update(
            session.scalars(
                select(SpaceProfileRef.space_id).where(
                    SpaceProfileRef.user_id == user_id,
                    SpaceProfileRef.status == "active",
                )
            )
        )
        ids.update(
            session.scalars(
                select(SpaceMember.space_id).where(
                    SpaceMember.user_id == user_id,
                    SpaceMember.status == "active",
                )
            )
        )
    return sorted(ids)


def merge_duplicate_profile(
    session: Session,
    ctx: ActorContext,
    *,
    space_id: int,
    survivor_id: int,
    retired_id: int,
    confirm_same_person: bool = False,
) -> MergedProfile:
    """把空间内一对确认同一人的重复档案合并为唯一人物（survivor），处置 retired。

    判定口径复用 services/person_identity（第三个调用方：建档门禁、回溯审计、合并
    复核）。单事务（``command_transaction(immediate=True)``，与建档门禁同一并发合同）
    内按序执行：授权双向 custody → 状态门（双方 managed）→ 重复复核（none 拒绝、
    confirm 缺失拒绝）→ owner 义务预检 → 迁移改指向 → 吊销会话 → 删除 retired →
    ``profile.merged`` 事件 + audit 快照。

    - claimed 档案不可合并：认领本人与合并代管是两条不可混用的身份路径，引导走
      claim_dispute 人工兜底。
    - 合并不覆写 survivor 字段（归一永不覆写存储值；字段搬运不是本任务范围）。
    - retired 不存在时幂等成功（already_merged=True，防重放，不重复写事件）。
    """
    from app.commands.ownership import assert_no_owner_obligations

    if survivor_id == retired_id:
        raise_api_error(422, VALIDATION_ERROR, "survivor 与 retired 不能是同一份档案")

    actor = load_actor(session, ctx)
    with command_transaction(session, immediate=True):
        survivor = session.get(User, survivor_id)
        if survivor is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")
        custody.assert_can_edit(actor, survivor)

        retired = session.get(User, retired_id)
        if retired is None:
            # 幂等重放：retired 已被合并/删除 → 幂等成功，不重复写事件（design §5）
            return MergedProfile(
                survivor_id=survivor_id,
                retired_id=retired_id,
                moved={"space_profile_refs": 0, "source_facts": 0, "attachments": 0},
                already_merged=True,
            )
        custody.assert_can_edit(actor, retired)

        in_space = session.scalar(
            select(SpaceProfileRef.id).where(
                SpaceProfileRef.space_id == space_id,
                SpaceProfileRef.user_id == survivor.id,
                SpaceProfileRef.status == "active",
            )
        ) or session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == survivor.id,
                SpaceMember.status == "active",
            )
        )
        retired_in_space = session.scalar(
            select(SpaceProfileRef.id).where(
                SpaceProfileRef.space_id == space_id,
                SpaceProfileRef.user_id == retired.id,
                SpaceProfileRef.status == "active",
            )
        ) or session.scalar(
            select(SpaceMember.id).where(
                SpaceMember.space_id == space_id,
                SpaceMember.user_id == retired.id,
                SpaceMember.status == "active",
            )
        )
        if in_space is None or retired_in_space is None:
            raise_api_error(404, USER_NOT_FOUND, "资源不存在")

        # 状态门：合并只适用于双方均 managed（未认领）；任一 claimed 走 claim_dispute。
        # 无 Account 的行（撞名绑定中的 provisional 人物）同样拒绝：其身份处置权
        # 属于绑定确认流（commands/bindings.py），不得经合并路径旁路。
        for role, row in (("survivor", survivor), ("retired", retired)):
            status = row.account.status if row.account is not None else None
            if status != "managed":
                raise_api_error(
                    409,
                    IDENTITY_INVALID_TRANSITION,
                    "已认领档案不能合并，请走认领争议（claim dispute）人工兜底",
                    detail={
                        "role": role,
                        "user_id": row.id,
                        "status": status,
                        "resolution": "claim_dispute",
                    },
                )

        # 重复复核：none（同名不同人）一律拒绝；strong/weak 均要求显式确认
        strength = person_identity.match_strength(
            name_key=person_identity.normalize_person_name(survivor.name),
            birth_key=person_identity.canonical_birth(survivor.birth),
            other_name_key=person_identity.normalize_person_name(retired.name),
            other_birth_key=person_identity.canonical_birth(retired.birth),
        )
        if strength == person_identity.STRENGTH_NONE:
            raise_api_error(
                409,
                VALIDATION_ERROR,
                "两份档案的姓名或生日不同，不是同一人，拒绝合并",
                detail={"survivor_id": survivor.id, "retired_id": retired.id},
            )
        if not confirm_same_person:
            raise_api_error(
                409,
                PERSON_DUPLICATE_AMBIGUOUS,
                "合并是两步确认操作，请显式确认两份档案是同一人",
                detail={
                    "survivor_id": survivor.id,
                    "retired_id": retired.id,
                    "match_strength": strength,
                    "resolution": "confirm_same_person",
                },
            )

        # owner 义务预检（managed 档案不应持有空间；防御性兜底，同 delete_profile_core）
        assert_no_owner_obligations(session, ctx, retired.id)

        # ---- 迁移改指向清单预分类（只读；计数进 profile.merged payload）----
        space_ids = _active_identity_space_ids(session, survivor.id, retired.id)

        # provisional 空间引用是身份承载行，CASCADE 会静默丢失 → 改指向 survivor；
        # uq_space_profile_ref_pair 唯一：survivor 在该空间已有引用（重复对的常态）时，
        # 该空间身份槽位已由 survivor 占据，retired 的重复引用随之删除。
        survivor_ref_space_ids = set(
            session.scalars(
                select(SpaceProfileRef.space_id).where(SpaceProfileRef.user_id == survivor.id)
            )
        )
        retired_refs = list(
            session.scalars(
                select(SpaceProfileRef).where(
                    SpaceProfileRef.user_id == retired.id,
                    SpaceProfileRef.status == "active",
                )
            )
        )
        refs_to_repoint = [
            ref for ref in retired_refs if ref.space_id not in survivor_ref_space_ids
        ]
        refs_to_drop = [ref for ref in retired_refs if ref.space_id in survivor_ref_space_ids]

        # 档案照片随档案保留：user_id（照片归属）与 uploaded_by（上传者=self 时
        # NO ACTION 会阻塞删除）都改指向 survivor。
        attachments_to_move = list(
            session.query(Attachment).filter(Attachment.user_id == retired.id).all()
        )

        facts_to_repoint, facts_colliding = source_facts.classify_facts_for_identity_merge(
            session, retired_user_id=retired.id, survivor_user_id=survivor.id
        )

        moved = {
            "space_profile_refs": len(refs_to_repoint),
            "source_facts": len(facts_to_repoint),
            "attachments": len(attachments_to_move),
        }
        retired_snapshot = {
            "id": retired.id,
            "name": retired.name,
            "gender": retired.gender,
            "birth": retired.birth,
            "death": retired.death,
            "bio": retired.bio,
            "privacy_mode": retired.privacy_mode,
            "profile_status": retired.profile_status,
            "account_status": retired.account.status if retired.account is not None else None,
            "created_by": retired.created_by,
        }

        # profile.merged（跨空间聚合事件，space_id=None；空间范围在 payload.space_ids）。
        # 事件先行：source_fact.revised 的 payload 需携带 merged 事件 id（design §2.3）。
        merged_event = emit(
            session,
            event_type="profile.merged",
            aggregate_type="profile",
            aggregate_id=survivor.id,
            payload={
                "survivor_id": survivor.id,
                "retired_id": retired.id,
                "space_ids": space_ids,
                "moved": moved,
            },
            actor_account_id=ctx.account_id,
        )

        # ---- 迁移改指向（先改指向再删除，CASCADE 只兜底非身份承载行）----
        for ref in refs_to_repoint:
            ref.user_id = survivor.id
        for ref in refs_to_drop:
            session.delete(ref)
        for attachment in attachments_to_move:
            attachment.user_id = survivor.id
            if attachment.uploaded_by == retired.id:
                attachment.uploaded_by = survivor.id
        for fact in facts_to_repoint:
            source_facts.repoint_fact_for_identity_merge(
                session,
                fact,
                retired_user_id=retired.id,
                survivor_user_id=survivor.id,
                merged_event_id=merged_event.id,
                actor_account_id=ctx.account_id,
            )
        for fact in facts_colliding:
            # 碰撞行不能直接改指向（自环/撞 uq_source_facts_active）：confirmed/disputed
            # 走 FSM revoke 留事件痕迹（行随后随 retired CASCADE 消失）；proposed 留给 CASCADE。
            if fact.state in (source_facts.FACT_CONFIRMED, source_facts.FACT_DISPUTED):
                source_facts.transition_source_fact(
                    session, fact, source_facts.ACTION_REVOKE, actor_account_id=ctx.account_id
                )

        legacy_relations = session.scalars(
            select(Relation).where(Relation.created_by == retired.id)
        ).all()
        for relation in legacy_relations:
            relation.created_by = survivor.id

        # 吊销 retired 活跃会话（managed 但可能存在未完成首登的 refresh session）
        from app.services import refresh_session as refresh_session_service

        refresh_session_service.revoke_all_active(
            session, retired.id, ip=ctx.ip, reason="identity_merge"
        )

        session.delete(retired)  # flush 时级联删除账号/残留碰撞行等子行，audit 行保留（无 FK）
        try:
            session.flush()
        except Exception:
            # v2 §0.5 兜底：义务预检与 owner_id RESTRICT 之间的竞态窗口
            session.rollback()
            raise_api_error(
                409,
                OWNER_TRANSFER_REQUIRED,
                "该档案是家庭空间所有者，请先完成 owner 移交后再删除",
            )

        result = MergedProfile(
            survivor_id=survivor.id,
            retired_id=retired.id,
            moved=moved,
            already_merged=False,
        )
        audit.write_audit(
            session,
            action="profile_merged",
            actor_id=actor.id,
            target_id=survivor.id,
            ip=ctx.ip,
            detail={
                "survivor_id": result.survivor_id,
                "retired_id": result.retired_id,
                "retired_snapshot": retired_snapshot,
                "space_ids": space_ids,
                "moved": moved,
                "merged_event_id": merged_event.id,
            },
        )
    return result
