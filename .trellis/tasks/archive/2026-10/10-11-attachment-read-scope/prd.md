# 附件读取权限：空间上下文与类型披露

## Goal

附件列表与原图下载在当前请求空间内使用同一授权规则；图片仅由 photos 披露，链接仅由 attachments 披露。附件继续归属个人档案。

## Requirements

- 空间读取显式传 space_id；服务端验证请求者 active membership，再按该空间 evaluate/disclosed_categories。不得借其它空间的成员资格放行。
- 无 space_id 的读取只允许本人或仍有编辑权的代管者；不再作为跨空间可见性聚合入口。本人/代管个人管理可保持既有 URL。
- self_private/household_detail 保持既有读取语义；lineage_summary 按附件类型消费披露；none、pending 最小互见、provisional 最小节点不得靠类别开关读出附件。
- 列表逐类型过滤；原图只接受 image，独立复核相同权限。不可见资源返回安全 404。
- 前端使用 spaces.currentSpaceId 向列表与媒体请求传同一上下文；切换空间/人物即清空旧列表、预览和 object URL，迟到响应不能回写。
- 不改变上传、删除、附件归属；不增加数据库列、OCR、RAG 或 Agent 使用授权。本任务只完成已批准设计的第一阶段。

## Acceptance Criteria

- [x] 同一读者/目标在空间 A 与 B 的相反覆盖分别生效，列表和 raw 一致。
- [x] photos/attachments 两个开关交叉组合仅开放对应类型。
- [x] 非成员、pending、未知空间、遗漏空间的普通读者不能获取附件；本人个人读取保持兼容。
- [x] 前端空间切换后不显示旧上下文图片，迟到响应不回写，卸载释放 URL。
- [x] 受影响后端/前端回归、lint/type-check 通过；已有无关失败单列不顺手修。
