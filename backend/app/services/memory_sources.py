"""Memory provenance and authorization, shared by management and RAG.

Persisted verification is evidence about creation, never a cached read grant.
Materialization checks source lifecycle without a reader; request projection
additionally checks every dependency against the current reader and space.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import (
    MEMORY_SCOPE_FORBIDDEN,
    MEMORY_SENSITIVE_SCOPE_FORBIDDEN,
    MEMORY_STATE_CONFLICT,
    raise_api_error,
)
from app.models.account import Account
from app.models.agent import AgentMessage, AgentSession
from app.models.attachment import Attachment
from app.models.attachment_content import AgentContentGrant, AttachmentContentVersion
from app.models.memory import Memory, MemoryCandidate
from app.models.rag import RAGChunk, RAGDocument
from app.models.space import FamilySpace, SpaceMember
from app.models.user import User
from app.services import attachment_access, platform_roles, visibility
from app.utils.timeutil import utcnow

MemoryRecord = Memory | MemoryCandidate
SourceStatus = Literal["available", "deleted_snapshot", "unavailable", "unverified"]
MAX_SOURCE_DEPTH = 8
# high also forbids sharing; both high and local_required need a local provider.
SENSITIVITY_ORDER = {"normal": 0, "sensitive": 1, "local_required": 2, "high": 3}
_RAG_REF = re.compile(
    r"^rag-chunk:([1-9]\d*):([1-9]\d*):([1-9]\d*):([A-Za-z0-9_.-]{1,32}):([1-9]\d*)$"
)


_AUTHORIZED_DOCUMENT_SOURCE = re.compile(
    r"^attachment:(?P<attachment>[1-9]\d*):version:(?P<version>[1-9]\d*):grant:(?P<grant>[1-9]\d*)$"
)


def is_attachment_grant_source(source_id: str) -> bool:
    """source_id 是否是**附件授权**形状（而不是档案派生内容的形状）。

    `authorized_document` 同时承载两种来源，它们的授权机制不同：

    1. 附件授权（本任务）：`attachment:<a>:version:<v>:grant:<g>`，真源是
       `agent_content_grants`——由 `_authorized_document_readable` 复核；
    2. 档案派生内容：`attachment:<subject>:<categories>`，真源是主体的四级阶梯，
       由 `_profile_clearance_ok` 复核。

    两者不能混用同一道门：对第 2 类施加 grant 要求会让 profile 清空用例全部
    不可读（实测），对第 1 类跳过 grant 要求则是授权漏洞。因此判别条件精确到
    完整形状，而**不是**笼统的 `attachment:` 前缀。

    与 `_ELIGIBILITY_SQL` 里的 `NOT LIKE 'attachment:%:version:%:grant:%'` 是同
    一个判别式，两处必须一致。
    """
    return _AUTHORIZED_DOCUMENT_SOURCE.fullmatch(source_id) is not None


@dataclass(frozen=True)
class SourceAccess:
    status: SourceStatus
    allowed_scopes: tuple[str, ...] = ()

    @property
    def readable(self) -> bool:
        return self.status in ("available", "deleted_snapshot")


@dataclass(frozen=True)
class ResolvedSource:
    kind: str
    source_type: str
    source_id: str
    revision: int
    space_id: int | None
    message_id: int | None
    document_ref: str | None
    snapshot: dict[str, Any]
    quote: str


def quote_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExactChunkRef:
    """Server-owned evidence about the exact material included in a build."""

    document_id: int
    chunk_id: int
    source_type: str
    source_id: str
    source_revision: int
    index_version: str
    chunk_index: int
    content_hash: str

    def as_json(self) -> dict[str, Any]:
        return {"version": 1, **self.__dict__}

    @classmethod
    def parse(cls, value: Any) -> ExactChunkRef | None:
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        for key in ("document_id", "chunk_id", "source_revision", "chunk_index"):
            number = value.get(key)
            if type(number) is not int or number < (0 if key == "chunk_index" else 1):
                return None
        for key, maximum in (("source_type", 32), ("source_id", 255), ("index_version", 32)):
            item = value.get(key)
            if not isinstance(item, str) or not 1 <= len(item) <= maximum:
                return None
        digest = value.get("content_hash")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return None
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class AuthorizedChunk:
    document: RAGDocument
    chunk: RAGChunk


def _authorized_document_readable(
    db: Session,
    document: RAGDocument,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str,
) -> bool:
    """Recheck the grant and the current attachment visibility projection."""
    match = _AUTHORIZED_DOCUMENT_SOURCE.fullmatch(document.source_id)
    if match is None:
        return False
    grant = db.get(AgentContentGrant, int(match["grant"]))
    version = db.get(AttachmentContentVersion, int(match["version"]))
    attachment = db.get(Attachment, int(match["attachment"]))
    target = db.get(User, attachment.user_id) if attachment is not None else None
    if (
        grant is None
        or version is None
        or attachment is None
        or target is None
        or grant.status != "active"
        or grant.agent_kind != agent_kind
        or grant.purpose != "rag"
        or grant.space_id != space_id
        or grant.content_version_id != version.id
        or version.attachment_id != attachment.id
        or version.status != "confirmed"
        or attachment.user_id != document.owner_user_id
    ):
        return False
    return attachment.type in attachment_access.readable_attachment_types(
        db,
        viewer=actor,
        account=account,
        target=target,
        space_id=space_id,
    )


def read_exact_chunk(
    db: Session,
    ref: ExactChunkRef,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str = "assistant",
    for_model: bool = False,
    provider_kind: str | None = None,
    require_active_index: bool = False,
) -> AuthorizedChunk | None:
    """Read the original fragment, then recheck the existing source policy.

    An active document pointer controls new searches only. A retained old-version
    chunk remains a valid historical dependency; missing/drifted data never gets
    substituted by a current search result.
    """
    document = db.get(RAGDocument, ref.document_id, populate_existing=True)
    chunk = db.get(RAGChunk, ref.chunk_id, populate_existing=True)
    if (
        document is None
        or chunk is None
        or chunk.document_id != ref.document_id
        or document.source_type != ref.source_type
        or document.source_id != ref.source_id
        or document.revision != ref.source_revision
        or document.source_revision != ref.source_revision
        or chunk.source_revision != ref.source_revision
        or chunk.index_version != ref.index_version
        or chunk.chunk_index != ref.chunk_index
        or chunk.status != "active"
        or quote_hash(chunk.text) != ref.content_hash
        or (require_active_index and document.index_version != ref.index_version)
        or (
            for_model
            and document.sensitivity in ("high", "local_required")
            and provider_kind != "local"
        )
        or not document_readable(
            db, document, actor=actor, account=account, space_id=space_id, agent_kind=agent_kind
        )
    ):
        return None
    return AuthorizedChunk(document=document, chunk=chunk)


def active_member(db: Session, user_id: int, space_id: int) -> bool:
    return (
        db.scalar(
            select(SpaceMember.id).where(
                SpaceMember.user_id == user_id,
                SpaceMember.space_id == space_id,
                SpaceMember.status == "active",
            )
        )
        is not None
    )


def original_user_text(message: AgentMessage) -> str | None:
    """Only the raw user-text shape can become an independent snapshot."""
    payload = message.content_json
    if message.role != "user" or not isinstance(payload, dict) or set(payload) != {"text"}:
        return None
    value = payload.get("text")
    return value if isinstance(value, str) and value.strip() else None


def normalize_source(
    source: dict[str, Any] | None,
    *,
    source_message_id: int | None = None,
    source_document_ref: str | None = None,
    source_span: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt only unambiguous old locators; never infer manual from omission."""
    if source_span:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "来源快照由服务端生成，请刷新客户端")
    if source is not None:
        if source_message_id is not None or source_document_ref is not None:
            raise_api_error(422, MEMORY_STATE_CONFLICT, "新旧来源字段不能同时提交")
        return dict(source)
    if source_message_id is not None and source_document_ref is None:
        return {"kind": "agent_message", "message_id": source_message_id}
    if source_document_ref is not None and source_message_id is None:
        match = _RAG_REF.fullmatch(source_document_ref)
        if match is not None:
            document_id, chunk_id, revision, index_version, space_id = match.groups()
            return {
                "kind": "rag_chunk",
                "document_id": int(document_id),
                "chunk_id": int(chunk_id),
                "revision": int(revision),
                "index_version": index_version,
                "space_id": int(space_id),
            }
    raise_api_error(422, MEMORY_STATE_CONFLICT, "记忆需要明确且可验证的来源，请刷新客户端")


def _raw_quote(row: MemoryRecord) -> str:
    return row.raw_quote if isinstance(row, Memory) else row.source_quote


def _confirmed_snapshot(row: MemoryRecord) -> bool:
    return isinstance(row, Memory) or (
        row.status == "confirmed"
        and row.confirmed_at is not None
        and row.confirmed_by_account_id == row.author_account_id
    )


def _active_memory(memory: Memory) -> bool:
    return (
        memory.status == "active"
        and memory.confirmation_status == "confirmed"
        and (memory.retention_until is None or memory.retention_until > utcnow())
    )


def _source_documents(
    db: Session,
    row: MemoryRecord,
    *,
    seen: frozenset[int] = frozenset(),
    require_live_message: bool = False,
) -> tuple[RAGDocument, ...] | None:
    snapshot = row.source_span_json
    if (
        row.source_verification != "verified"
        or not isinstance(snapshot, dict)
        or snapshot.get("version") != 1
        or snapshot.get("kind") != row.source_kind
        or snapshot.get("quote_sha256") != quote_hash(_raw_quote(row))
    ):
        return None
    if row.source_kind == "manual":
        return () if snapshot.get("author_account_id") == row.author_account_id else None
    if row.source_kind == "agent_message":
        if (
            row.source_type != "agent_message"
            or str(snapshot.get("message_id")) != row.source_id
            or snapshot.get("author_account_id") != row.author_account_id
        ):
            return None
        # The explicitly confirmed original user text is independent of live chat.
        if _confirmed_snapshot(row) and not require_live_message:
            return ()
        message = (
            db.get(AgentMessage, row.source_message_id, populate_existing=True)
            if row.source_message_id
            else None
        )
        session = (
            db.get(AgentSession, message.session_id, populate_existing=True) if message else None
        )
        if (
            message is None
            or session is None
            or session.account_id != row.author_account_id
            or message.id != snapshot.get("message_id")
            or session.space_id != row.source_space_id
            or original_user_text(message) != _raw_quote(row)
        ):
            return None
        return ()
    if row.source_kind != "rag_chunk":
        return None
    document_id, chunk_id = snapshot.get("document_id"), snapshot.get("chunk_id")
    if not isinstance(document_id, int) or not isinstance(chunk_id, int):
        return None
    document = db.get(RAGDocument, document_id, populate_existing=True)
    chunk = db.get(RAGChunk, chunk_id, populate_existing=True)
    if (
        document is None
        or chunk is None
        or chunk.document_id != document.id
        or document.source_type != row.source_type
        or document.source_id != row.source_id
        or document.revision != row.source_revision
        or chunk.source_revision != row.source_revision
        or chunk.index_version != snapshot.get("index_version")
        or chunk.status != "active"
        or document.scope != snapshot.get("scope")
        or document.space_id != row.source_space_id
        or document.author_account_id != snapshot.get("author_account_id")
        or quote_hash(chunk.text) != snapshot.get("quote_sha256")
    ):
        return None
    documents = _document_chain(db, document, seen=seen)
    if documents is None or any(
        SENSITIVITY_ORDER.get(row.sensitivity, -1) < SENSITIVITY_ORDER.get(doc.sensitivity, 99)
        for doc in documents
    ):
        return None
    return documents


def _document_chain(
    db: Session,
    document: RAGDocument,
    *,
    seen: frozenset[int] = frozenset(),
) -> tuple[RAGDocument, ...] | None:
    if (
        document.id in seen
        or len(seen) >= MAX_SOURCE_DEPTH
        or document.status != "active"
        or document.confirmation_status not in ("confirmed", "authorized")
    ):
        return None
    if document.author_account_id is not None:
        author = db.get(Account, document.author_account_id, populate_existing=True)
        if author is None or db.get(User, author.user_id, populate_existing=True) is None:
            return None
    if document.source_type != "memory":
        return (document,)
    if not document.source_id.isdigit():
        return None
    memory = db.get(Memory, int(document.source_id), populate_existing=True)
    if (
        memory is None
        or not _active_memory(memory)
        or memory.revision != document.revision
        or memory.author_account_id != document.author_account_id
        or memory.scope != document.scope
        or memory.space_id != document.space_id
        or memory.sensitivity != document.sensitivity
    ):
        return None
    dependencies = _source_documents(db, memory, seen=seen | {document.id})
    return None if dependencies is None else (document, *dependencies)


def source_lifecycle(db: Session, row: MemoryRecord) -> Literal["valid", "unverified", "invalid"]:
    """No reader/membership checks: safe for background index eligibility."""
    db.flush()
    if row.source_verification != "verified":
        return "unverified"
    if isinstance(row, Memory) and not _active_memory(row):
        return "invalid"
    return "valid" if _source_documents(db, row) is not None else "invalid"


def memory_materializable(db: Session, memory: Memory) -> bool:
    return source_lifecycle(db, memory) == "valid"


def _author_visible(db: Session, actor: User, account_id: int | None, space_id: int | None) -> bool:
    if platform_roles.is_platform_operator(db, actor.account):
        return False
    if account_id is None:
        return True
    author = db.scalar(
        select(User).join(Account, Account.user_id == User.id).where(Account.id == account_id)
    )
    return (
        author is not None
        and visibility.evaluate(
            db,
            actor,
            author,
            space_context=space_id,
            purpose=visibility.PURPOSE_RAG,
        ).visible
    )


def _can_read_document(
    db: Session,
    document: RAGDocument,
    actor: User,
    account: Account,
    space_id: int | None,
    agent_kind: str,
    private_reader_account_id: int | None,
) -> bool:
    if document.scope == "private":
        # private 的语义是「**只能被它的作者账号读**」，不是「哪些 kind 能读」。
        # 旧写法把两者绑在一起（`agent_kind != "assistant"`），而管家的空间级 kind
        # 会回落到 space admin 作为身份——一旦为管家打开 private，它就会读到管理员
        # 本人的私事。改成显式读者后，无法提供读者（NULL）就是恒不可读。
        # 这一层与 `_ELIGIBILITY_SQL` 的 private 分支必须**各自独立**挡住：
        # 任何一层单独失效都不得让私有记忆漏出。
        if document.author_account_id != private_reader_account_id:
            return False
    elif document.scope in ("household", "lineage"):
        if document.space_id is None or (space_id is not None and document.space_id != space_id):
            return False
        if not active_member(db, actor.id, document.space_id):
            return False
    elif document.scope != "public" or agent_kind != "assistant":
        return False
    # 附件授权文档的第三重交集（grant × 内容版本 × 当前附件可见性）。
    #
    # 只对**附件授权形状**的 source_id 施加：档案派生内容（main 的
    # visibility_snapshot 清空声明）由下面的 `_profile_clearance_ok` 裁决，两者是
    # 同一 `source_type` 下的两种授权机制，不能混用同一道门。
    if document.source_type == "authorized_document" and is_attachment_grant_source(
        document.source_id
    ):
        if space_id is None or not _authorized_document_readable(
            db,
            document,
            actor=actor,
            account=account,
            space_id=space_id,
            agent_kind=agent_kind,
        ):
            return False
    if not _profile_clearance_ok(db, document, actor, space_id):
        return False
    return _author_visible(db, actor, document.author_account_id, space_id or document.space_id)


def _profile_clearance_ok(
    db: Session, document: RAGDocument, actor: User, space_id: int | None
) -> bool:
    """声明了档案清空要求的内容，由**主体的四级阶梯**裁决。

    `visibility_snapshot` 为空时直接放行：`memory` / `public_kinship` 走旧的 scope
    裁决，行为逐字不变（scope 是主体选过的可见性，不叠加掩码）。

    非空时用 `visibility.evaluate` 重算当前读者的 decision，再要求它清空声明的类别。
    这里**不复制**阶梯、`HIGH_RISK_CATEGORIES` 或未成年人 overlay —— 判据全在
    `visibility.py`，本层只负责调用。

    `visibility_snapshot_key` 与声明里的 key 不符 → 不可读（fail closed）：
    判据含义变过之后，旧声明不能被新语义重新解释。
    """
    snapshot = document.visibility_snapshot or {}
    subject_user_id = snapshot.get("subject_user_id")
    if subject_user_id is None:
        return True
    if snapshot.get("key") != visibility.PROFILE_CLEARANCE_KEY:
        return False
    if document.visibility_snapshot_key != visibility.PROFILE_CLEARANCE_KEY:
        return False
    subject = db.get(User, int(subject_user_id))
    if subject is None:
        return False
    decision = visibility.evaluate(
        db,
        actor,
        subject,
        space_context=space_id or document.space_id,
        purpose=visibility.PURPOSE_RAG,
    )
    required = snapshot.get("required_categories") or []
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        return False
    return visibility.required_clearance_satisfied(decision, required)


#: `private_reader_account_id` 未传时的哨兵：区分「调用方没表态」（→ 用调用方自己的
#: `account.id`，即 assistant 语义）与「显式表态没有私有读者」（→ NULL，恒不可读）。
#: 用专属类型而不是 `object()` 是为了让类型检查器能通过 isinstance 收窄。
class _UnsetReader:
    __slots__ = ()


_UNSET_READER = _UnsetReader()


def document_readable(
    db: Session,
    document: RAGDocument,
    *,
    actor: User,
    account: Account,
    space_id: int,
    agent_kind: str = "assistant",
    private_reader_account_id: int | None | _UnsetReader = _UNSET_READER,
) -> bool:
    reader = (
        account.id
        if isinstance(private_reader_account_id, _UnsetReader)
        else private_reader_account_id
    )
    db.flush()
    documents = _document_chain(db, document)
    return (
        account.user_id == actor.id
        and active_member(db, actor.id, space_id)
        and documents is not None
        and all(
            _can_read_document(db, doc, actor, account, space_id, agent_kind, reader)
            for doc in documents
        )
    )


def _scope_options(db: Session, actor: User, sensitivity: str) -> set[str]:
    if platform_roles.is_platform_operator(db, actor.account):
        return set()
    result = {"private"}
    if sensitivity != "high":
        rows = db.execute(
            select(FamilySpace.kind, FamilySpace.id)
            .join(
                SpaceMember,
                SpaceMember.space_id == FamilySpace.id,
            )
            .where(SpaceMember.user_id == actor.id, SpaceMember.status == "active")
        ).all()
        result.update(
            f"{kind}:{space_id}" for kind, space_id in rows if kind in ("household", "lineage")
        )
    return result


def _restrict_scopes(scopes: set[str], documents: tuple[RAGDocument, ...]) -> tuple[str, ...]:
    for document in documents:
        if document.scope == "private" or document.sensitivity == "high":
            scopes.intersection_update({"private"})
        elif document.scope in ("household", "lineage"):
            scopes.intersection_update({"private", f"{document.scope}:{document.space_id}"})
    return tuple(sorted(scopes))


def document_allowed_scopes(db: Session, document: RAGDocument, actor: User) -> tuple[str, ...]:
    documents = _document_chain(db, document)
    return (
        ()
        if documents is None
        else _restrict_scopes(_scope_options(db, actor, document.sensitivity), documents)
    )


def source_access(
    db: Session,
    row: MemoryRecord,
    *,
    actor: User,
    account: Account,
    space_id: int | None = None,
    require_live_message: bool = False,
) -> SourceAccess:
    db.flush()
    if row.source_verification != "verified":
        return SourceAccess("unverified")
    documents = _source_documents(db, row, require_live_message=require_live_message)
    if (
        documents is None
        or account.user_id != actor.id
        or platform_roles.is_platform_operator(db, account)
    ):
        return SourceAccess("unavailable")
    context = space_id
    if context is None and row.source_kind == "rag_chunk":
        value = row.source_span_json.get("access_space_id")
        context = value if isinstance(value, int) else row.source_space_id
    if any(
        not _can_read_document(db, doc, actor, account, context, "assistant", account.id)
        for doc in documents
    ):
        return SourceAccess("unavailable")
    if row.source_kind == "rag_chunk" and (
        context is None or not active_member(db, actor.id, context)
    ):
        return SourceAccess("unavailable")
    if row.source_kind == "agent_message" and (
        require_live_message or not _confirmed_snapshot(row)
    ):
        if row.source_space_id is None or not active_member(db, actor.id, row.source_space_id):
            return SourceAccess("unavailable")
    scopes = _scope_options(db, actor, row.sensitivity)
    if row.source_kind == "agent_message":
        scopes = {
            value
            for value in scopes
            if value == "private" or value.endswith(f":{row.source_space_id}")
        }
    allowed = _restrict_scopes(scopes, documents) if row.author_account_id == account.id else ()
    if row.source_kind == "agent_message" and _confirmed_snapshot(row):
        message = (
            db.get(AgentMessage, row.source_message_id, populate_existing=True)
            if row.source_message_id
            else None
        )
        if message is None:
            return SourceAccess("deleted_snapshot", allowed)
    return SourceAccess("available", allowed)


def memory_access(
    db: Session,
    memory: Memory,
    *,
    actor: User,
    account: Account,
    space_id: int | None = None,
) -> SourceAccess:
    if memory.scope == "private":
        if memory.author_account_id != account.id:
            return SourceAccess("unavailable")
    elif (
        memory.space_id is None
        or (space_id is not None and space_id != memory.space_id)
        or not active_member(db, actor.id, memory.space_id)
    ):
        return SourceAccess("unavailable")
    if not _author_visible(db, actor, memory.author_account_id, space_id or memory.space_id):
        return SourceAccess("unavailable")
    return source_access(db, memory, actor=actor, account=account, space_id=space_id)


def resolve_source(
    db: Session,
    *,
    account: Account,
    source: dict[str, Any],
    raw_quote: str | None,
    sensitivity: str,
) -> ResolvedSource:
    actor = db.get(User, account.user_id)
    if actor is None or platform_roles.is_platform_operator(db, account):
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "无权使用此记忆来源")
    kind = source.get("kind")
    captured_at = utcnow().isoformat()
    if kind == "manual" and set(source) == {"kind"}:
        if raw_quote is None or not raw_quote.strip():
            raise_api_error(422, MEMORY_STATE_CONFLICT, "手工记忆需要本人输入原文")
        return ResolvedSource(
            "manual",
            "manual",
            str(uuid.uuid4()),
            1,
            None,
            None,
            None,
            {
                "version": 1,
                "kind": "manual",
                "author_account_id": account.id,
                "captured_at": captured_at,
                "quote_sha256": quote_hash(raw_quote),
            },
            raw_quote,
        )
    if kind == "agent_message" and set(source) == {"kind", "message_id"}:
        message_id = source.get("message_id")
        message = (
            db.get(AgentMessage, message_id, populate_existing=True)
            if isinstance(message_id, int)
            else None
        )
        session = (
            db.get(AgentSession, message.session_id, populate_existing=True) if message else None
        )
        quote = original_user_text(message) if message else None
        if (
            session is None
            or session.account_id != account.id
            or quote is None
            or not active_member(db, actor.id, session.space_id)
        ):
            raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "只能保存本人获权会话中的原始用户消息")
        if raw_quote is not None and raw_quote != quote:
            raise_api_error(422, MEMORY_STATE_CONFLICT, "原文与来源消息不一致")
        assert message is not None
        return ResolvedSource(
            "agent_message",
            "agent_message",
            str(message.id),
            1,
            session.space_id,
            message.id,
            None,
            {
                "version": 1,
                "kind": "agent_message",
                "message_id": message.id,
                "session_id": session.id,
                "session_space_id": session.space_id,
                "author_account_id": account.id,
                "message_created_at": message.created_at.isoformat(),
                "captured_at": captured_at,
                "quote_sha256": quote_hash(quote),
            },
            quote,
        )
    required = {"kind", "document_id", "chunk_id", "revision", "index_version", "space_id"}
    if (
        kind != "rag_chunk"
        or set(source) != required
        or any(
            not isinstance(source[key], int) or source[key] <= 0
            for key in ("document_id", "chunk_id", "revision", "space_id")
        )
        or not isinstance(source["index_version"], str)
    ):
        raise_api_error(422, MEMORY_STATE_CONFLICT, "来源定位字段不合法")
    document = db.get(RAGDocument, source["document_id"], populate_existing=True)
    chunk = db.get(RAGChunk, source["chunk_id"], populate_existing=True)
    if (
        document is None
        or chunk is None
        or chunk.document_id != document.id
        or document.revision != source["revision"]
        or chunk.source_revision != source["revision"]
        or chunk.index_version != source["index_version"]
        or chunk.status != "active"
        or not document_readable(
            db, document, actor=actor, account=account, space_id=source["space_id"]
        )
    ):
        raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "来源已失效或当前无权读取，请重新检索")
    documents = _document_chain(db, document)
    assert documents is not None
    if any(
        SENSITIVITY_ORDER[sensitivity] < SENSITIVITY_ORDER[doc.sensitivity] for doc in documents
    ):
        raise_api_error(422, MEMORY_SENSITIVE_SCOPE_FORBIDDEN, "保存记忆不能降低来源敏感等级")
    if raw_quote is not None and raw_quote != chunk.text:
        raise_api_error(422, MEMORY_STATE_CONFLICT, "原文与检索来源不一致")
    ref = (
        f"rag-chunk:{document.id}:{chunk.id}:{document.revision}:"
        f"{chunk.index_version}:{source['space_id']}"
    )
    return ResolvedSource(
        "rag_chunk",
        document.source_type,
        document.source_id,
        document.revision,
        document.space_id,
        None,
        ref,
        {
            "version": 1,
            "kind": "rag_chunk",
            "document_id": document.id,
            "chunk_id": chunk.id,
            "revision": document.revision,
            "index_version": chunk.index_version,
            "scope": document.scope,
            "space_id": document.space_id,
            "access_space_id": source["space_id"],
            "author_account_id": document.author_account_id,
            "captured_at": captured_at,
            "quote_sha256": quote_hash(chunk.text),
            "dependencies": [
                {
                    "document_id": doc.id,
                    "source_type": doc.source_type,
                    "source_id": doc.source_id,
                    "revision": doc.revision,
                    "space_id": doc.space_id,
                    "scope": doc.scope,
                }
                for doc in documents
            ],
        },
        chunk.text,
    )


def apply_source(row: MemoryRecord, source: ResolvedSource) -> None:
    row.source_kind = source.kind
    row.source_verification = "verified"
    row.source_type = source.source_type
    row.source_id = source.source_id
    row.source_revision = source.revision
    row.source_space_id = source.space_id
    row.source_message_id = source.message_id
    row.source_document_ref = source.document_ref
    row.source_span_json = dict(source.snapshot)


def verify_legacy_source(db: Session, row: MemoryRecord, *, account: Account) -> bool:
    """Explicit repair seam: only independently verifiable historical sources.

    Does not confirm, change scope, or index. Unknown labels remain quarantined.
    The caller owns the transaction and decides when to rematerialize the index.
    """
    if row.author_account_id != account.id or row.source_verification != "unverified":
        return False
    source = normalize_source(
        None, source_message_id=row.source_message_id, source_document_ref=row.source_document_ref
    )
    resolved = resolve_source(
        db, account=account, source=source, raw_quote=_raw_quote(row), sensitivity=row.sensitivity
    )
    if isinstance(row, Memory):
        actor = db.get(User, account.user_id)
        assert actor is not None
        allowed = _scope_options(db, actor, row.sensitivity)
        if resolved.kind == "agent_message":
            allowed = {
                scope
                for scope in allowed
                if scope == "private" or scope.endswith(f":{resolved.space_id}")
            }
        elif resolved.kind == "rag_chunk":
            document = db.get(RAGDocument, resolved.snapshot["document_id"])
            assert document is not None
            allowed.intersection_update(document_allowed_scopes(db, document, actor))
        requested_scope = row.scope if row.scope == "private" else f"{row.scope}:{row.space_id}"
        if requested_scope not in allowed:
            raise_api_error(403, MEMORY_SCOPE_FORBIDDEN, "存量记忆范围超过可验证来源，不能自动恢复")
    previous_span = row.source_span_json
    apply_source(row, resolved)
    if previous_span:
        row.source_span_json = {**row.source_span_json, "legacy_source_span": previous_span}
    return True


def public_source_snapshot(row: MemoryRecord) -> dict[str, Any]:
    """Bounded attribution fields only; old arbitrary client spans never escape."""
    allowed = {
        "version",
        "kind",
        "message_id",
        "session_id",
        "session_space_id",
        "document_id",
        "chunk_id",
        "revision",
        "index_version",
        "scope",
        "space_id",
        "captured_at",
        "message_created_at",
    }
    return {key: value for key, value in row.source_span_json.items() if key in allowed}
