"""存量 overlay 缓存中的冲突推测边：读取即排除（09-19 修复 S7 兼容）。

模拟「修复前已缓存的 overlay 行」：该行 input_versions 仍匹配、未过期、witness
路径合法，只是其中含一条与已确认亲子事实冲突的 sibling 边。正常路径在证据变化时
会让整行失效，因此这条过滤只对升级前既有缓存（及判据本身变化）生效。

已实测守护力：去掉 `payload_for` 中的冲突过滤后，本用例返回
`['direct_sibling']` 即失败；恢复后通过。
"""

from datetime import timedelta

from test_steward_inferred import _member, _platform_on, _space_flag_on

from app.models.steward import StewardInferredOverlay
from app.models.steward_inferred import StewardInferredEdge
from app.services import steward_overlay, steward_snapshot
from app.services.source_facts import create_source_fact, transition_source_fact
from app.utils import timeutil
from conftest import create_agent_fixture


def test_legacy_cached_overlay_row_with_conflicting_edge_is_not_served(db_session, monkeypatch):
    _platform_on(monkeypatch)
    _account, space = create_agent_fixture(db_session, name="probe9")
    _space_flag_on(db_session, space.id)
    viewer = _member(db_session, space, "p9-viewer", "m")
    other = _member(db_session, space, "p9-other", "f")
    f = create_source_fact(
        db_session,
        fact_type="biological_parent",
        subject_user_id=viewer.id,
        object_user_id=other.id,
        provenance="manual_entry",
        space_id=space.id,
    )
    transition_source_fact(db_session, f, "confirm")
    now = timeutil.utcnow()
    edge = StewardInferredEdge(
        space_id=space.id,
        subject_user_id=viewer.id,
        object_user_id=other.id,
        relation_kind="direct_sibling",
        status="proposed",
        origin="llm",
        evidence_hash="legacy",
        evidence_json={"facts": []},
        revision=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(edge)
    db_session.commit()

    step = {
        "from": viewer.id,
        "to": other.id,
        "edge_type": "sibling",
        "subtype": None,
        "direction": "sym",
        "fact_id": -edge.id,
    }
    versions = steward_snapshot.input_versions(db_session, space.id)
    payload = {
        "nodes": [{"user_id": viewer.id}, {"user_id": other.id}],
        "edges": [
            {
                "id": edge.id,
                "revision": edge.revision,
                "relation_kind": "direct_sibling",
                "subject_user_id": viewer.id,
                "object_user_id": other.id,
                "path": [step],
                "viewer_path": [],
                "new_user_id": None,
            }
        ],
        "evidence_steps": [list(step.values())],
    }
    db_session.add(
        StewardInferredOverlay(
            space_id=space.id,
            viewer_account_id=viewer.account.id,
            input_versions_json=versions,
            payload_json=payload,
            valid_until=now + timedelta(hours=1),
            updated_at=now,
        )
    )
    db_session.commit()

    nodes, edges, revision = steward_overlay.payload_for(
        db_session,
        account_id=viewer.account.id,
        space_id=space.id,
        root_user_id=viewer.id,
        core_nodes=[{"user_id": viewer.id, "inclusion_reason_code": "root"}],
    )
    assert edges == []
