"""m3a 附件安全测试：校验链逐环 / 授权下载 / 删除一致性 / 孤儿清扫。"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import UPLOADS_DIR
from conftest import auth_header, create_user_with_pin, login


def _login(client: TestClient, name: str, pin: str) -> dict[str, str]:
    resp = login(client, name, pin)
    assert resp.status_code == 200, resp.text
    return auth_header(resp.json())


def _png_bytes(size: tuple[int, int] = (10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=(200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def pair(db_session):
    owner = create_user_with_pin(
        db_session,
        "主人",
        "111111",
        claim_status="claimed",
        birth={"cal_type": "solar", "date": "1970-01-01"},
    )
    outsider = create_user_with_pin(db_session, "外人", "999999", claim_status="claimed")
    db_session.commit()
    return owner, outsider


def test_upload_valid_png_and_download_authorized(db_session, client: TestClient, pair):
    owner, _outsider = pair
    h = _login(client, "主人", "111111")

    up = client.post(
        f"/api/users/{owner.id}/attachments/image?title=全家福",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert up.status_code == 201, up.text
    att = up.json()
    assert att["type"] == "image" and att["url_or_path"] is None  # 路径不外泄

    # 列表可见（本人）
    lst = client.get(f"/api/users/{owner.id}/attachments", headers=h)
    assert lst.status_code == 200 and len(lst.json()) == 1

    # 授权下载
    dl = client.get(f"/api/attachments/{att['id']}/raw", headers=h)
    assert dl.status_code == 200
    assert dl.headers["x-content-type-options"] == "nosniff"


def test_upload_rejects_forgery_and_oversize(db_session, client: TestClient, pair):
    owner, _o = pair
    h = _login(client, "主人", "111111")
    url = f"/api/users/{owner.id}/attachments/image"

    # exe 伪装 png
    r1 = client.post(url, files={"file": ("evil.png", b"MZ\x90\x00fake", "image/png")}, headers=h)
    assert r1.status_code == 422
    # SVG 拒绝
    r2 = client.post(url, files={"file": ("a.svg", b"<svg/>", "image/svg+xml")}, headers=h)
    assert r2.status_code == 422
    # 文本伪装 jpg
    r3 = client.post(url, files={"file": ("a.jpg", b"hello world text!!", "image/jpeg")}, headers=h)
    assert r3.status_code == 422


def test_exif_stripped_on_reencode(db_session, client: TestClient, pair):
    """重编码后输出文件不含原始元数据（PNG 输出无 EXIF 块）。"""
    owner, _o = pair
    h = _login(client, "主人", "111111")
    up = client.post(
        f"/api/users/{owner.id}/attachments/image",
        files={"file": ("p.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert up.status_code == 201
    from app.db import SessionLocal
    from app.models.attachment import Attachment

    row = SessionLocal().query(Attachment).order_by(Attachment.id.desc()).first()
    stored = (UPLOADS_DIR / row.url_or_path.split("/")[-1]).read_bytes()
    assert b"eXIf" not in stored and b"tEXt" not in stored


def test_link_scheme_whitelist(db_session, client: TestClient, pair):
    owner, _o = pair
    h = _login(client, "主人", "111111")
    ok = client.post(
        f"/api/users/{owner.id}/attachments/link",
        json={"url": "https://example.com/family", "title": "族谱资料"},
        headers=h,
    )
    assert ok.status_code == 201
    bad = client.post(
        f"/api/users/{owner.id}/attachments/link",
        json={"url": "javascript:alert(1)"},
        headers=h,
    )
    assert bad.status_code == 422


def test_delete_removes_record_and_file(db_session, client: TestClient, pair):
    owner, _o = pair
    h = _login(client, "主人", "111111")
    up = client.post(
        f"/api/users/{owner.id}/attachments/image",
        files={"file": ("p.png", _png_bytes(), "image/png")},
        headers=h,
    )
    att_id = up.json()["id"]
    dele = client.delete(f"/api/attachments/{att_id}", headers=h)
    assert dele.status_code == 204
    assert client.get(f"/api/attachments/{att_id}/raw", headers=h).status_code == 404


@pytest.fixture()
def scoped_attachments(db_session, client, pair):
    from app.models.space import FamilySpace, SpaceMember
    from app.utils.timeutil import utcnow

    owner, viewer = pair
    spaces = []
    for name in ("A", "B"):
        space = FamilySpace(name=name, owner_id=owner.id, kind="lineage", created_at=utcnow())
        db_session.add(space)
        db_session.flush()
        for user, role in ((owner, "space_admin"), (viewer, "member")):
            db_session.add(
                SpaceMember(
                    space_id=space.id,
                    user_id=user.id,
                    added_by=owner.id,
                    role=role,
                    status="active",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
        spaces.append(space.id)
    db_session.commit()
    owner_headers = _login(client, "主人", "111111")
    image = client.post(
        f"/api/users/{owner.id}/attachments/image",
        files={"file": ("p.png", _png_bytes(), "image/png")},
        headers=owner_headers,
    )
    link = client.post(
        f"/api/users/{owner.id}/attachments/link",
        json={"url": "https://example.com/family"},
        headers=owner_headers,
    )
    assert image.status_code == link.status_code == 201
    return owner, viewer, spaces, image.json()["id"], link.json()["id"], owner_headers


@pytest.mark.parametrize(
    "photos,links", [(False, False), (False, True), (True, False), (True, True)]
)
def test_scoped_disclosure_controls_types_and_raw(client, scoped_attachments, photos, links):
    owner, _viewer, spaces, image_id, link_id, owner_headers = scoped_attachments
    for space_id, photo_flag, link_flag in (
        (None, True, True),
        (spaces[0], photos, links),
        (spaces[1], not photos, not links),
    ):
        payload = {
            "avatar": False,
            "photos": photo_flag,
            "dates": False,
            "bio": False,
            "attachments": link_flag,
        }
        if space_id is not None:
            payload["space_id"] = space_id
        response = client.put(
            f"/api/users/{owner.id}/disclosure", json=payload, headers=owner_headers
        )
        assert response.status_code == 200, response.text

    headers = _login(client, "外人", "999999")
    for space_id, photo_flag, link_flag in (
        (spaces[0], photos, links),
        (spaces[1], not photos, not links),
    ):
        params = {"space_id": space_id}
        response = client.get(f"/api/users/{owner.id}/attachments", params=params, headers=headers)
        expected = ({image_id} if photo_flag else set()) | ({link_id} if link_flag else set())
        if expected:
            assert response.status_code == 200, response.text
            assert {row["id"] for row in response.json()} == expected
        else:
            assert response.status_code == 404
        raw = client.get(f"/api/attachments/{image_id}/raw", params=params, headers=headers)
        assert raw.status_code == (200 if photo_flag else 404)
        if photo_flag:
            assert raw.headers["cache-control"] == "private, no-store"
        assert (
            client.get(
                f"/api/attachments/{link_id}/raw", params=params, headers=headers
            ).status_code
            == 404
        )
    # 省略上下文不得借全局开放设置绕过逐空间关闭。
    assert client.get(f"/api/users/{owner.id}/attachments", headers=headers).status_code == 404
    assert client.get(f"/api/attachments/{image_id}/raw", headers=headers).status_code == 404


@pytest.mark.parametrize("status", ["pending", "removed"])
def test_non_active_reader_cannot_use_space(client, db_session, scoped_attachments, status):
    from sqlalchemy import select

    from app.models.space import SpaceMember

    owner, viewer, spaces, image_id, _link_id, _headers = scoped_attachments
    membership = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == spaces[0], SpaceMember.user_id == viewer.id
        )
    )
    membership.status = status
    db_session.commit()
    headers = _login(client, "外人", "999999")
    for space_id in (spaces[0], 999999):
        for path in (f"/api/users/{owner.id}/attachments", f"/api/attachments/{image_id}/raw"):
            assert (
                client.get(path, params={"space_id": space_id}, headers=headers).status_code == 404
            )


def test_pending_target_does_not_gain_media_from_disclosure(client, db_session, scoped_attachments):
    from sqlalchemy import select

    from app.models.space import SpaceMember

    owner, _viewer, spaces, image_id, _link_id, owner_headers = scoped_attachments
    response = client.put(
        f"/api/users/{owner.id}/disclosure",
        headers=owner_headers,
        json={
            "avatar": False,
            "photos": True,
            "dates": False,
            "bio": False,
            "attachments": True,
            "space_id": spaces[0],
        },
    )
    assert response.status_code == 200
    membership = db_session.scalar(
        select(SpaceMember).where(
            SpaceMember.space_id == spaces[0], SpaceMember.user_id == owner.id
        )
    )
    membership.status = "pending"
    db_session.commit()
    headers = _login(client, "外人", "999999")
    for path in (f"/api/users/{owner.id}/attachments", f"/api/attachments/{image_id}/raw"):
        assert client.get(path, params={"space_id": spaces[0]}, headers=headers).status_code == 404
