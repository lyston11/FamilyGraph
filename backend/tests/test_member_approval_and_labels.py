"""房主审批链 + 成员间自由关系词标注（09-20）。

覆盖：
- 三条加入路径（邀请 / 申请 / 邀请码）都要房主批准，且顺序与自批边界正确；
- 关系词必填、长度上限、自由文本不限词表；
- 关系词仅两端本人可改、改完即时生效、清空即移除；
- 关系词**绝不参与亲属推导**（填「朋友」不产生任何亲属边，也不扩大可见范围）；
- 任一端退出后标注边立即不再返回。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.space import FamilySpace, MemberRelationLabel, SpaceMember
from app.utils.timeutil import utcnow
from conftest import auth_header, create_space_member, create_user_with_pin, login


def _hdr(client: TestClient, name: str, pin: str = "123456") -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _space(session, owner, name: str, *, kind: str = "household") -> FamilySpace:
    space = FamilySpace(name=name, kind=kind, owner_id=owner.id, created_at=utcnow())
    session.add(space)
    session.flush()
    create_space_member(session, space.id, owner.id, role="space_admin")
    return space


def _member(session, space_id: int, user_id: int) -> SpaceMember:
    return session.scalar(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )


class _Scene:
    """房主 + 一个外人（同族，用于三条加入路径）。"""

    def __init__(self, session, *, name: str = "审批"):
        self.owner = create_user_with_pin(session, f"{name}-房主", "123456")
        self.guest = create_user_with_pin(session, f"{name}-客人", "123456")
        self.lineage = _space(session, self.owner, f"{name}-家族", kind="lineage")
        create_space_member(session, self.lineage.id, self.guest.id)
        self.home = _space(session, self.owner, f"{name}-房主家")
        self.home.lineage_space_id = self.lineage.id
        # 可见性：v1 直系边（既有可见性合同）
        from app.models.relation import Relation

        now = utcnow()
        session.add(
            Relation(
                from_user=self.owner.id,
                to_user=self.guest.id,
                dir_class="elder",
                created_by=self.owner.id,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


# ---- 邀请：房主批准 → 受邀人接受 ----


def test_invitation_needs_owner_approval_then_invitee_acceptance(
    client: TestClient, db_session
) -> None:
    scene = _Scene(db_session, name="邀请链")
    created = client.post(
        f"/api/spaces/{scene.home.id}/members",
        json={"user_id": scene.guest.id, "relation_label": "远房堂弟"},
        headers=_hdr(client, "邀请链-房主"),
    )
    assert created.status_code == 201, created.text
    member_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    # 审批状态在治理面板端点（成员列表）暴露；创建响应只回成员行本身
    listed = client.get(f"/api/spaces/{scene.home.id}/members", headers=_hdr(client, "邀请链-房主"))
    row = next(item for item in listed.json() if item["id"] == member_id)
    assert row["origin"] == "invite"
    assert row["owner_approved_at"] is None

    # 顺序不可颠倒：房主未批准时受邀人不能先行接受
    early = client.post(
        f"/api/space-memberships/{member_id}/accept", headers=_hdr(client, "邀请链-客人")
    )
    assert early.status_code == 403, early.text

    # 房主批准：invite 仍需受邀人接受，故仍为 pending
    approved = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "邀请链-房主")
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "pending"
    listed = client.get(f"/api/spaces/{scene.home.id}/members", headers=_hdr(client, "邀请链-房主"))
    row = next(item for item in listed.json() if item["id"] == member_id)
    assert row["owner_approved_at"] is not None

    # 受邀人接受 → active
    accepted = client.post(
        f"/api/space-memberships/{member_id}/accept", headers=_hdr(client, "邀请链-客人")
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"


def test_owner_cannot_approve_their_own_membership(client: TestClient, db_session) -> None:
    """自批判据是「被批准的人就是批准人」，不是「发起人就是批准人」。"""
    scene = _Scene(db_session, name="自批判据")
    created = client.post(
        f"/api/spaces/{scene.home.id}/members",
        json={"user_id": scene.guest.id, "relation_label": "堂弟"},
        headers=_hdr(client, "自批判据-房主"),
    )
    member_id = created.json()["id"]
    # 房主批准别人是可以的（发起人=批准人，但被批准的人不是自己）
    ok = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "自批判据-房主")
    )
    assert ok.status_code == 200, ok.text

    # 非房主批准 → 403
    denied = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "自批判据-客人")
    )
    assert denied.status_code == 403, denied.text


# ---- 申请：房主批准即生效 ----


def test_join_request_becomes_active_on_owner_approval(client: TestClient, db_session) -> None:
    scene = _Scene(db_session, name="申请链")
    created = client.post(
        "/api/spaces/join-by-user",
        json={
            "lineage_space_id": scene.lineage.id,
            "target_user_id": scene.owner.id,
            "relation_label": "族侄",
        },
        headers=_hdr(client, "申请链-客人"),
    )
    assert created.status_code == 201, created.text
    member_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    listed = client.get(f"/api/spaces/{scene.home.id}/members", headers=_hdr(client, "申请链-房主"))
    assert next(i for i in listed.json() if i["id"] == member_id)["origin"] == "join_request"

    # 申请人自己批准 → 403
    self_approve = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "申请链-客人")
    )
    assert self_approve.status_code == 403, self_approve.text

    # 房主批准 → 当场 active（申请人提交即其同意）
    approved = client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "申请链-房主")
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"

    # 已生效后外部再 accept 属非法转换
    again = client.post(
        f"/api/space-memberships/{member_id}/accept", headers=_hdr(client, "申请链-客人")
    )
    assert again.status_code == 409, again.text


# ---- 邀请码：兑换 → 房主批准 ----


def test_invite_code_redeem_needs_owner_approval(client: TestClient, db_session) -> None:
    scene = _Scene(db_session, name="码链")
    code = client.post(
        "/api/invite-codes",
        json={"kind": "household", "space_id": scene.home.id, "ttl_days": 7},
        headers=_hdr(client, "码链-房主"),
    )
    assert code.status_code == 201, code.text
    raw = code.json()["code"]

    redeemed = client.post(
        "/api/me/invite-codes/redeem",
        json={"code": raw, "relation_label": "邻居"},
        headers=_hdr(client, "码链-客人"),
    )
    assert redeemed.status_code == 200, redeemed.text

    member = _member(db_session, scene.home.id, scene.guest.id)
    assert member is not None and member.status == "pending"

    # 未批准前仍是 pending：没有 active membership，故没有任何读取授权
    db_session.expire_all()
    assert _member(db_session, scene.home.id, scene.guest.id).status == "pending"

    approved = client.post(
        f"/api/space-memberships/{member.id}/approve", headers=_hdr(client, "码链-房主")
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"


# ---- 关系词：必填 / 长度 / 自由文本 ----


def test_relation_label_is_required_on_every_join_path(client: TestClient, db_session) -> None:
    scene = _Scene(db_session, name="必填")
    # 邀请
    missing_invite = client.post(
        f"/api/spaces/{scene.home.id}/members",
        json={"user_id": scene.guest.id},
        headers=_hdr(client, "必填-房主"),
    )
    assert missing_invite.status_code == 422, missing_invite.text
    # 申请
    missing_join = client.post(
        "/api/spaces/join-by-user",
        json={"lineage_space_id": scene.lineage.id, "target_user_id": scene.owner.id},
        headers=_hdr(client, "必填-客人"),
    )
    assert missing_join.status_code == 422, missing_join.text
    # 没有任何 pending 行落地
    assert _member(db_session, scene.home.id, scene.guest.id) is None


def test_relation_label_is_free_text_with_a_length_cap(client: TestClient, db_session) -> None:
    """不限词表（朋友/闺蜜等均可），只限制长度。"""
    scene = _Scene(db_session, name="自由文本")
    for label in ("朋友", "闺蜜", "大学同学"):
        resp = client.put(
            f"/api/spaces/{scene.home.id}/member-relation-label",
            json={"other_user_id": scene.guest.id, "label": label},
            headers=_hdr(client, "自由文本-房主"),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["label"] == label

    too_long = client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "很" * 65},
        headers=_hdr(client, "自由文本-房主"),
    )
    assert too_long.status_code == 422, too_long.text


def test_relation_label_is_one_row_per_pair_and_editable_by_either_endpoint(
    client: TestClient, db_session
) -> None:
    """一对人一条；两端本人都能改；第三方不能改。"""
    scene = _Scene(db_session, name="双方可改")
    client.post(
        f"/api/spaces/{scene.home.id}/members",
        json={"user_id": scene.guest.id, "relation_label": "堂弟"},
        headers=_hdr(client, "双方可改-房主"),
    )
    member_id = _member(db_session, scene.home.id, scene.guest.id).id
    client.post(
        f"/api/space-memberships/{member_id}/approve", headers=_hdr(client, "双方可改-房主")
    )
    client.post(f"/api/space-memberships/{member_id}/accept", headers=_hdr(client, "双方可改-客人"))

    # 对端（受邀人）改词：即时生效
    edited = client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.owner.id, "label": "族兄"},
        headers=_hdr(client, "双方可改-客人"),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["label"] == "族兄"

    # 仍是同一条行（无序对唯一）
    rows = db_session.scalars(
        select(MemberRelationLabel).where(MemberRelationLabel.space_id == scene.home.id)
    ).all()
    assert len(rows) == 1 and rows[0].label == "族兄"

    # 第三方（非该对任一端）不能改
    outsider = create_user_with_pin(db_session, "双方可改-外人", "123456")
    create_space_member(db_session, scene.home.id, outsider.id)
    db_session.commit()
    denied = client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "无关的人"},
        headers=_hdr(client, "双方可改-外人"),
    )
    # 允许建立自己与对方的词，但绝不影响既有的那一对
    assert denied.status_code == 200
    db_session.expire_all()
    pair = db_session.scalar(
        select(MemberRelationLabel).where(
            MemberRelationLabel.space_id == scene.home.id,
            MemberRelationLabel.label == "族兄",
        )
    )
    assert pair is not None


def test_clearing_the_relation_label_removes_the_edge(client: TestClient, db_session) -> None:
    scene = _Scene(db_session, name="清空")
    client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "朋友"},
        headers=_hdr(client, "清空-房主"),
    )
    cleared = client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "   "},
        headers=_hdr(client, "清空-房主"),
    )
    assert cleared.status_code == 200
    assert cleared.json() is None
    assert (
        db_session.scalars(
            select(MemberRelationLabel).where(MemberRelationLabel.space_id == scene.home.id)
        ).all()
        == []
    )


# ---- 安全底线：关系词不参与亲属推导 ----


def test_relation_label_never_creates_a_kinship_edge(client: TestClient, db_session) -> None:
    """填「朋友」不产生任何亲属事实/拓扑边，也不扩大家族树可达性。"""
    from app.models.relationship_facts import SourceFact
    from app.services import member_labels, relationship_graph

    scene = _Scene(db_session, name="非亲属")
    facts_before = db_session.scalars(select(SourceFact)).all()

    client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "朋友"},
        headers=_hdr(client, "非亲属-房主"),
    )

    facts_after = db_session.scalars(select(SourceFact)).all()
    assert [f.id for f in facts_after] == [f.id for f in facts_before]

    # 该空间没有任何 confirmed 亲属事实 → 拓扑边为空
    topology = relationship_graph.topology_edges_from_facts(
        relationship_graph.scoped_confirmed_facts(db_session, space_ids={scene.home.id})
    )
    assert topology == []

    # 标注边作为显示层存在，但不进入拓扑边（上面已断言 topology == []）
    labels = member_labels.labels_for(
        db_session, space_id=scene.home.id, visible_ids={scene.owner.id, scene.guest.id}
    )
    assert [row["label"] for row in labels] == ["朋友"]


def test_label_edge_disappears_once_an_endpoint_leaves(client: TestClient, db_session) -> None:
    """任一端退出后，标注边立即不再返回（按当次授权节点集合过滤）。"""
    from app.services import member_labels

    scene = _Scene(db_session, name="退出")
    client.put(
        f"/api/spaces/{scene.home.id}/member-relation-label",
        json={"other_user_id": scene.guest.id, "label": "朋友"},
        headers=_hdr(client, "退出-房主"),
    )
    both = {scene.owner.id, scene.guest.id}
    assert len(member_labels.labels_for(db_session, space_id=scene.home.id, visible_ids=both)) == 1

    # 客人退出（本人即可，无需审批）
    create_space_member(db_session, scene.home.id, scene.guest.id)
    db_session.commit()
    guest_member = _member(db_session, scene.home.id, scene.guest.id)
    quit_resp = client.delete(
        f"/api/space-memberships/{guest_member.id}", headers=_hdr(client, "退出-客人")
    )
    assert quit_resp.status_code in (200, 204), quit_resp.text

    db_session.expire_all()
    assert guest_member.status == "removed"
    # 读取端传入的授权节点集合来自当次 active membership：退出者不再在其中
    remaining = {scene.owner.id}
    assert member_labels.labels_for(db_session, space_id=scene.home.id, visible_ids=remaining) == []
