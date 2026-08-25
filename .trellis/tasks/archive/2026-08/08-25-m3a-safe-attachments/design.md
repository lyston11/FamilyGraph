# m3a 技术设计

> 契约：architecture.md §6（附件行）§9（AD-7 校验链）。

- alembic 0007：attachments(id,user_id FK CASCADE,type CHECK(image|link|location),
  url_or_path,title,description,uploaded_by FK,created_at)
- 上传校验链：扩展名白名单(jpg/jpeg/png/webp)→≤10MB→magic bytes→Pillow verify→
  像素≤8000²→重编码 strip EXIF→存 UPLOADS_DIR/{uuid}.webp（统一转 webp 简化）
- 端点：POST /users/{id}/attachments/image(multipart)、POST /users/{id}/attachments/link、
  GET /users/{id}/attachments（按可见性：full 或 summary+attachments 披露）、
  GET /attachments/{id}/raw（授权流式，nosniff）、DELETE /attachments/{id}（编辑权主体）
- 清扫脚本 python -m app.cleanup：删除 uploads 中无 DB 引用的孤儿文件
