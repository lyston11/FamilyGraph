"""附件安全服务（m3a，AD-7 校验链 + 存储一致性）。

上传校验链：扩展名白名单 → ≤10MB → magic bytes → Pillow verify → 像素上限
→ 重编码 strip EXIF（统一转 PNG 存储以剥离全部元数据）。SVG 一律拒绝。
删除一致性：事务删记录 → 物理文件异步/延后删除 → 清扫脚本兜底。
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


from PIL import Image, UnidentifiedImageError

from app.config import UPLOADS_DIR
from app.errors import raise_api_error

MAX_BYTES = 10 * 1024 * 1024  # 10MB
MAX_PIXELS = 8000 * 8000
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def validate_image_upload(filename: str, data: bytes) -> None:
    """逐环校验；任一失败即 422 ATTACHMENT_INVALID。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "svg" or filename.lower().endswith(".svg"):
        raise_api_error(422, "ATTACHMENT_INVALID", "不支持 SVG 图片")
    if ext not in ALLOWED_EXTENSIONS:
        raise_api_error(422, "ATTACHMENT_INVALID", f"不支持的图片格式 .{ext}")
    if len(data) > MAX_BYTES:
        raise_api_error(422, "ATTACHMENT_TOO_LARGE", "图片不能超过 10MB")
    if len(data) < 12:
        raise_api_error(422, "ATTACHMENT_INVALID", "文件内容不是有效图片")
    if ext == "webp":
        magic_ok = data[0:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        expected = {"jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG"}[ext]
        magic_ok = data.startswith(expected)
    if not magic_ok:
        raise_api_error(422, "ATTACHMENT_INVALID", "文件内容与扩展名不符")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except (UnidentifiedImageError, Exception):  # noqa: BLE001 - Pillow 各类解析异常统一拒绝
        raise_api_error(422, "ATTACHMENT_INVALID", "图片无法解码")
    # verify 后需重新打开读取尺寸
    with Image.open(io.BytesIO(data)) as img2:
        w, h = img2.size
    if w * h > MAX_PIXELS:
        raise_api_error(422, "ATTACHMENT_TOO_LARGE", "图片像素超出上限")


def reencode_strip_metadata(data: bytes) -> tuple[bytes, str]:
    """重编码为 PNG（strip EXIF 等元数据），返回 (bytes, 扩展名)。"""
    with Image.open(io.BytesIO(data)) as img:
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        buf = io.BytesIO()
        clean.save(buf, format="PNG")
        return buf.getvalue(), "png"


def save_image(user_id: int, data: bytes) -> Path:
    """写入 UPLOADS_DIR/{uuid}.png；目录惰性创建。"""
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOADS_DIR / f"{uuid.uuid4().hex}.png"
    path.write_bytes(data)
    void: int = user_id  # 参数留作审计扩展位
    del void
    return path


def delete_file_quiet(url_or_path: str) -> bool:
    """物理删除；仅允许删除 uploads 内的相对路径（防路径穿越）。"""
    try:
        p = Path(url_or_path)
        resolved = p.resolve() if p.is_absolute() else (UPLOADS_DIR / p.name).resolve()
        if UPLOADS_DIR.resolve() not in resolved.parents:
            return False
        if resolved.exists():
            resolved.unlink()
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def validate_link_url(url: str) -> None:
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    if scheme not in ("http", "https"):
        raise_api_error(422, "ATTACHMENT_INVALID", "链接必须以 http:// 或 https:// 开头")


def sweep_orphans(session: Session) -> int:
    """清扫 uploads 中无 attachments 记录引用的孤儿文件。返回清理数。"""
    from sqlalchemy import select

    from app.models.attachment import Attachment

    referenced = {
        Path(row[0]).name
        for row in session.execute(select(Attachment.url_or_path)).all()
        if row[0] and not row[0].startswith(("http://", "https://"))
    }
    removed = 0
    if UPLOADS_DIR.exists():
        for f in UPLOADS_DIR.iterdir():
            if f.is_file() and f.name not in referenced:
                f.unlink()
                removed += 1
    return removed
