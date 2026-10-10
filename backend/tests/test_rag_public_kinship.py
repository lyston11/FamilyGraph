"""`public_kinship` 纵向切片：唯一 `scope='public'` 的真实来源。

## 这个来源为什么存在

`RAG_SOURCE_TYPES` 声明五类，实际只有 `memory` 有写入方，因此 `scope='public'`
在生产**恒为空**——`_ELIGIBILITY_SQL` 的 public 分支从未被验证过。本文件把它
变成可验证的。

选内置称谓包（`term_entries` 的 system/locale 级）作为第一个真实接入，理由是它
**不含任何个人数据**：这是它可以是 `scope='public'` 的唯一理由，也让它成为唯一
零隐私风险的切片。

## 这些断言在防什么

| 断言 | 防的缺陷 |
|---|---|
| public 文档三列为 NULL、正文受限 | 「public」被当成更宽的一层，把含个人数据的文档投成公开 |
| 幂等 | 重复 seed 产生第二份投影，citation handle 漂移 |
| 同 revision 异内容 → 409 | 内容被静默改写而 handle 不变（引用失效） |
| 撤权后查询不可见、行仍在 | 误以为「撤权 = 删索引」；实测索引不承载授权 |
| 已 `index_superseded` 的公共文档不得重新激活 | 公共来源借用 memory 的历史恢复语义 |
| steward 读不到 | 二维可读集把 `public` 也放开（`:is_assistant = 1` 被绕过） |
"""

from __future__ import annotations

import re

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.models.platform_features import PlatformFeatureConfig
from app.models.rag import RAGDocument
from app.services import memory_rag, terms
from conftest import create_agent_fixture

#: 正文行的合法形状：标题句，或「概念编码 表示 称谓。」。
#: 这条断言是「不含个人数据」的**结构性**证明，比「不含某个具体字符串」强。
#:
#: 注意两个真实形状约束：正文是**一段连续文本**（chunker 不做硬换行，只在句末
#: 切分），且内置称谓全部是纯汉字。因此断言必须对**完整文本**做「只含这些形状」
#: 的匹配，而不是逐行匹配——逐行匹配会把「一整段」误判为非法。
_HEADER_RE = re.compile(r"^家谱称谓知识包 [A-Za-z][A-Za-z-]*：以下概念编码对应的亲属称谓。$")
_ENTRY_RE = re.compile(r"^[A-Za-z][A-Za-z-]* 表示 [\u4e00-\u9fff]+。$")

#: 每个 chunk 的**字符允许集**。chunk 之间的重叠是 100 字符的原文尾部切片
#: （见 `_chunk_text`），因此不能要求 chunk 以句首开始；但重叠片段仍是同一字母表
#: 的子串，所以「只含这些字符」这条判据在 chunk 上依然成立，且它才是
#: 「正文不含个人数据」的结构性证明——姓名、账号 id、路径都不在此集合内。
_ALLOWED_RE = re.compile(r"^[A-Za-z\u4e00-\u9fff：。\- ]+$")


def _enable_rag(db) -> None:
    from app.models.platform_features import PlatformFeatureConfig
    from app.utils import timeutil

    row = db.get(PlatformFeatureConfig, 1)
    if row is None:
        row = PlatformFeatureConfig(id=1, updated_at=timeutil.utcnow())
        db.add(row)
    row.memory_enabled = True
    row.rag_enabled = True
    db.commit()


def _documents(db) -> list[RAGDocument]:
    return list(
        db.scalars(
            select(RAGDocument)
            .where(RAGDocument.source_type == "public_kinship")
            .order_by(RAGDocument.id)
        )
    )


def _chunks(db, document: RAGDocument):
    from app.models.rag import RAGChunk

    return list(
        db.scalars(
            select(RAGChunk)
            .where(RAGChunk.document_id == document.id)
            .order_by(RAGChunk.chunk_index)
        )
    )


@pytest.fixture()
def rag_on(db_session):
    _enable_rag(db_session)
    terms.seed_builtin_packs(db_session)
    db_session.commit()
    return db_session


def test_seed_creates_public_documents_without_personal_data(rag_on):
    """内置种子在生产路径上真的建了 `scope='public'` 文档，且不含个人数据。"""
    documents = _documents(rag_on)
    assert documents, "种子必须在生产路径上建立公共称谓索引（不是只在测试里）"
    for document in documents:
        assert document.scope == "public"
        assert document.space_id is None
        # 这三列是 `public` 声明的可测形式：没有任何读者身份可归属。
        assert document.author_account_id is None
        assert document.owner_user_id is None
        assert document.confirmation_status == "authorized"
        assert document.sensitivity == "normal"
        assert document.source_id.startswith(memory_rag.PUBLIC_KINSHIP_SOURCE_PREFIX)
        assert document.revision == document.source_revision
        for chunk in _chunks(rag_on, document):
            assert chunk.text.strip(), chunk.text
            assert _ALLOWED_RE.match(
                chunk.text
            ), f"正文含允许集之外的字符（可能是姓名/账号 id/路径）：{chunk.text[:120]}"


def test_public_kinship_index_is_idempotent(rag_on):
    """同内容重复物化返回同一文档，不产生第二份投影。"""
    before = [(row.source_id, row.revision) for row in _documents(rag_on)]
    assert before
    assert memory_rag.index_public_kinship_packs(rag_on) == 0
    rag_on.commit()
    assert [(row.source_id, row.revision) for row in _documents(rag_on)] == before


def test_same_revision_different_content_is_rejected(rag_on):
    """同一 revision 配不同正文必须 409，不能静默改写已有引用。"""
    locale = "zh-CN"
    document = rag_on.scalar(
        __import__("sqlalchemy")
        .select(RAGDocument)
        .where(
            RAGDocument.source_type == "public_kinship",
            RAGDocument.source_id == memory_rag.public_kinship_source_id(locale),
        )
    )
    assert document is not None
    revision = document.revision
    # 伪造内容必须落在**同一个** revision 上，才能测「同版本异内容」这条判据。
    # 不能靠改 revision：那会走成「新版本」，是另一条路径。
    forged = [
        (row.concept_code, row.term)
        for row in rag_on.execute(
            text(
                "SELECT concept_code, term FROM term_entries "
                "WHERE level IN ('system','locale') AND status = 'active' "
                "AND (locale = :locale OR (:locale = 'system' AND locale IS NULL))"
            ),
            {"locale": locale},
        ).all()
    ]
    forged.append(("ZZZ", "伪造称谓"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            memory_rag,
            "public_kinship_revision",
            lambda entries: revision,
        )
        with pytest.raises(HTTPException) as error:
            memory_rag.index_public_kinship(rag_on, locale=locale, entries=forged)
    assert error.value.status_code == 409
    rag_on.rollback()
    assert document.revision == revision


def test_revocation_hides_the_source_but_keeps_the_rows(rag_on):
    """撤权后带过滤查询不再返回；行仍在——可见性由查询层承担，不靠删除。"""
    user, space = create_agent_fixture(rag_on, name="public-kinship-revoke")
    document = _documents(rag_on)[0]
    assert (
        memory_rag.search_rag(
            rag_on,
            actor=user,
            account=user.account,
            space_id=space.id,
            query="外婆",
            for_model=False,
        )
        != []
    ), "撤权前必须能召回公共称谓"

    # 全部公共包一起撤权：语料里有多份包，只撤一份时别的包仍会命中同一个查询词。
    for row in _documents(rag_on):
        memory_rag.invalidate_source(rag_on, source_type="public_kinship", source_id=row.source_id)
    rag_on.commit()

    assert (
        memory_rag.search_rag(
            rag_on,
            actor=user,
            account=user.account,
            space_id=space.id,
            query="外婆",
            for_model=False,
        )
        == []
    ), "撤权后不得再返回该来源"
    # 反证：行仍然在，只是被查询层过滤掉（索引不承载授权）。
    row = rag_on.execute(
        text("SELECT status, invalidation_reason FROM rag_documents WHERE id = :id"),
        {"id": document.id},
    ).one()
    assert row[0] == "invalidated"
    assert row[1] == "source_invalidated"
    assert rag_on.scalar(select(RAGDocument.id).where(RAGDocument.id == document.id)) is not None


def test_index_superseded_public_document_is_not_reactivated(rag_on):
    """公共来源不得借用 memory 的 `index_superseded` 历史恢复语义。"""
    from app.utils.timeutil import utcnow

    document = _documents(rag_on)[0]
    document.status = "invalidated"
    document.invalidation_reason = "index_superseded"
    document.invalidated_at = utcnow()
    rag_on.commit()
    entries = [
        (row.concept_code, row.term)
        for row in rag_on.execute(
            __import__("sqlalchemy").text(
                "SELECT concept_code, term FROM term_entries "
                "WHERE level IN ('system','locale') AND status = 'active' "
                "AND (locale = :locale OR (:locale = 'system' AND locale IS NULL))"
            ),
            {"locale": document.source_id[len(memory_rag.PUBLIC_KINSHIP_SOURCE_PREFIX) :]},
        ).all()
    ]
    with pytest.raises(HTTPException) as error:
        memory_rag.index_public_kinship(
            rag_on, locale=document.source_id.split(":", 1)[1], entries=entries
        )
    assert error.value.status_code == 409
    rag_on.rollback()


def test_assistant_recalls_public_kinship_with_a_citation(rag_on):
    """assistant 能召回公共称谓并拿到合法 citation handle。"""
    user, space = create_agent_fixture(rag_on, name="public-kinship-assistant")
    hits = memory_rag.search_rag(
        rag_on,
        actor=user,
        account=user.account,
        space_id=space.id,
        query="外婆 舅舅 称谓",
        for_model=False,
    )
    public = [hit for hit in hits if hit.source_type == "public_kinship"]
    assert public, f"未召回公共称谓：{[h.source_type for h in hits]}"
    for hit in public:
        assert hit.scope == "public"
        assert hit.citation_handle.startswith("rag:term-pack:")
        assert hit.text.strip()


def test_steward_cannot_read_public_kinship(rag_on):
    """`public` 分支由 `:is_assistant = 1` 控制：二维可读集不得绕过该边界。"""
    user, space = create_agent_fixture(rag_on, name="public-kinship-steward")
    hits = memory_rag.search_rag(
        rag_on,
        actor=user,
        account=user.account,
        space_id=space.id,
        query="外婆 舅舅 称谓",
        agent_kind="steward",
        for_model=False,
    )
    assert [hit for hit in hits if hit.source_type == "public_kinship"] == []


def test_maintenance_backfills_public_kinship_exactly_once(db_session, monkeypatch):
    """既有安装（迁移早已跑完、种子不会重跑）由维护循环补建，且只建一次。"""
    from app.services import maintenance

    # 夹具不跑种子，因此这里天然就是「迁移已跑完但公共索引不存在」的既有安装形态。
    terms.seed_builtin_packs(db_session)
    db_session.commit()
    for document in _documents(db_session):
        db_session.delete(document)
    db_session.commit()
    assert _documents(db_session) == []
    _enable_rag(db_session)

    monkeypatch.setattr(maintenance.config, "RAG_ENABLED", True)
    first = maintenance.run_maintenance_tick()
    assert first["rag_public_kinship_packs"] >= 1
    assert _documents(db_session) != []

    second = maintenance.run_maintenance_tick()
    assert second["rag_public_kinship_packs"] == 0, "建成后必须恒为 no-op"


def test_maintenance_does_not_resurrect_revoked_public_kinship(db_session, monkeypatch):
    """运维撤权必须是**持久**的：维护补建不得每个 tick 重试并刷 409 告警。

    这条防的是一个真实的失败形态：补建判据若只看 `status='active'`，撤权后的每个
    维护 tick 都会重试一次 `index_public_kinship`，而它对该状态恒抛 409 →
    每 5 秒一条 WARNING 永久刷屏，掩盖真实告警。判据改成「有没有任何投影」后，
    撤权与 `index_superseded` 的既有语义一致：不可由维护循环复活。
    """
    from app.services import maintenance

    terms.seed_builtin_packs(db_session)
    db_session.commit()
    _enable_rag(db_session)
    for document in _documents(db_session):
        memory_rag.invalidate_source(
            db_session, source_type="public_kinship", source_id=document.source_id
        )
    db_session.commit()
    revoked = {document.source_id: document.status for document in _documents(db_session)}
    assert revoked and set(revoked.values()) == {"invalidated"}

    monkeypatch.setattr(maintenance.config, "RAG_ENABLED", True)
    counters = maintenance.run_maintenance_tick()

    assert counters["rag_public_kinship_packs"] == 0, "撤权的语料不得被补建复活"
    assert {document.source_id: document.status for document in _documents(db_session)} == revoked


def test_fts_repair_keeps_public_kinship_consistent(rag_on):
    """`repair_fts` 不改内容、不改变检索结果（索引是可重建派生物）。"""
    user, space = create_agent_fixture(rag_on, name="public-kinship-repair")
    query = "外婆 舅舅 称谓"
    before = [
        hit.chunk_id
        for hit in memory_rag.search_rag(
            rag_on,
            actor=user,
            account=user.account,
            space_id=space.id,
            query=query,
            for_model=False,
        )
    ]
    assert before
    memory_rag.repair_fts(rag_on)
    rag_on.commit()
    after = [
        hit.chunk_id
        for hit in memory_rag.search_rag(
            rag_on,
            actor=user,
            account=user.account,
            space_id=space.id,
            query=query,
            for_model=False,
        )
    ]
    assert after == before
    # 公共文档的 chunk 必须都在 FTS 投影里（否则它只能靠 LIKE 后备命中）。
    public_ids = {
        chunk.id for document in _documents(rag_on) for chunk in _chunks(rag_on, document)
    }
    indexed = set(rag_on.scalars(text("SELECT chunk_id FROM rag_chunks_fts")))
    assert public_ids <= indexed


def test_rendered_text_is_only_header_and_entries():
    """渲染出的完整正文只由标题句与词条句组成——不含任何自由文本字段。"""
    text_value = memory_rag.render_public_kinship_text(
        "zh-CN", [("Uf-Bm", "舅舅"), ("U-U", "祖父母")]
    )
    lines = text_value.split("\n")
    assert _HEADER_RE.match(lines[0]), lines[0]
    assert lines[1:] == ["U-U 表示 祖父母。", "Uf-Bm 表示 舅舅。"], lines[1:]
    for line in lines[1:]:
        assert _ENTRY_RE.match(line), line


def test_public_kinship_revision_is_content_derived():
    """版本号来自内容而非 `count(*)`/`max(updated_at)`：顺序无关、内容敏感。"""
    a = [("U-U", "祖父母"), ("Uf-Bm", "舅舅")]
    assert memory_rag.public_kinship_revision(a) == memory_rag.public_kinship_revision(
        list(reversed(a))
    )
    assert memory_rag.public_kinship_revision(a) != memory_rag.public_kinship_revision(
        [("U-U", "祖父母")]
    )
    assert memory_rag.public_kinship_revision(a) >= 1


def test_rag_disabled_seed_does_not_break_the_seed(rag_on):
    """RAG 关闭时种子仍必须成功（数据准备不由 RAG 开关决定成败）。"""
    from sqlalchemy import delete

    from app.models.rag import RAGChunk

    rag_on.execute(
        delete(RAGChunk).where(
            RAGChunk.document_id.in_([document.id for document in _documents(rag_on)])
        )
    )
    rag_on.execute(delete(RAGDocument).where(RAGDocument.source_type == "public_kinship"))
    rag_on.commit()
    from app.utils import timeutil

    row = rag_on.get(PlatformFeatureConfig, 1, populate_existing=True)
    row.rag_enabled = False
    row.updated_at = timeutil.utcnow()
    rag_on.commit()
    terms.seed_builtin_packs(rag_on)
    rag_on.commit()
    assert _documents(rag_on) == []
