# m3a 安全附件存储

> 父任务：[08-25-m3-media-lunar-stats-search](../08-25-m3-media-lunar-stats-search/prd.md)｜依赖：m2a（下载授权）｜安全边界：architecture.md §9 `[AD-7]`

## Goal

图片/链接附件与头像，按安全校验链落地。

## Requirements

- attachments 表（type: image|link|location 枚举占位；url_or_path/title/description/uploaded_by）。
- 图片上传校验链：扩展名白名单(jpg/jpeg/png/webp)→≤10MB→magic bytes→Pillow verify()→像素上限 8000×8000→重编码 strip EXIF；SVG 拒绝。
- 存储 /data/uploads 服务端生成文件名（防路径穿越）；删除=先事务删记录后异步删文件+孤儿清扫脚本。
- 档案页相册区：缩略图网格+放大+删除（权限=D5 编辑权者）；链接附件卡片列表。
- 外链 URL scheme 白名单 http/https，服务端不抓取（无 SSRF），前端 rel=noopener 外跳。
- 头像复用图片存储与上传校验链（待定决策 Q4 默认方案）；头像原图出口已受 m2a 矩阵管控。
- 新依赖 Pillow 锁版本入 pyproject。

## Acceptance Criteria

- [ ] jpg/png 上传成功显示；exe/SVG/超限/伪造扩展名的文件全部被拒且有明确文案。
- [ ] 含 EXIF GPS 的照片上传后输出文件无元数据。
- [ ] 删除记录后物理文件被清理；制造孤儿文件可被清扫脚本回收。
- [ ] javascript: 等危险 scheme 的外链被拒；相册对无权用户不可见（矩阵回归）。

## Non-goals

- 定位附件功能；视频/文档类型；对象存储。
