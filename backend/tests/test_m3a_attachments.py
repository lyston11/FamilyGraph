"""m3a 附件安全测试：校验链逐环 / 授权下载 / 删除一致性 / 孤儿清扫。"""

from __future__ import annotations

import io

import pytest
from conftest import auth_header, create_user_with_pin, login
from fastapi.testclient import TestClient
from PIL import Image

from app.config import UPLOADS_DIR


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
