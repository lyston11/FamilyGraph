# 设计

## 边界

缺口在 HTTP 附件读取的上下文与类型授权：api/attachments.py 的两个 GET 都无空间上下文，并把 image/link 都交给 attachments 开关。frontend/api/attachments.ts 与 AttachmentsSection.vue 未传空间，组件只在 mounted 加载。

## 后端

- 复用 family_projection.authorized_space_or_404 核验空间 active membership；evaluate/disclosed_categories 必须使用同一 space_id。
- visibility.py 的 VisibilityDecision 增加可选 source 字段（默认空以兼容既有构造）；evaluate 输出已有 _base_level 的来源，附件据此拒绝 pending/custodian_provisional。不复制 _base_level 的关系图判定，也不改变既有档案字段投影。
- api/attachments.py 共用附件允许类型的判断：无空间只允许本人或 custody.resolve_relation 的 edit 主体；空间请求先鉴权，再 evaluate。self/household 允许 image/link，lineage 仅映射 photos→image、attachments→link。未知类型默认拒绝。
- list 按允许类型筛选；raw 检查 image 及同一授权，Cache-Control: private, no-store。保留现有防枚举 404。

## 前端

- fetchAttachments(userId, spaceId?)，fetchAttachmentBlob 保持既有 signal 参数位置，增加末尾 spaceId 参数，params 传 space_id。
- AttachmentsSection 读 useSpacesStore().currentSpaceId，watch [userId, currentSpaceId] immediate 加载替代 onMounted。
- 每次加载先 revokeAll + 清空 items，递增加载代际；列表和图片请求使用捕获的同一上下文，回写前核对代际；卸载递增代际并清理。避免迟到 blob 创建 URL。

## 兼容与限制

无空间的本人请求继续工作；普通读者必须提供空间。代管个人入口保持编辑权检查，不把代管解释成空间成员资格。未成年人不引入本轮未定义的新照片禁令，沿用既有层级合同；未来内容提取需单独定义内容安全策略。

## 文件范围

backend/app/services/visibility.py（来源信息）、backend/app/api/attachments.py（共用读取规则）、backend/tests/test_m3a_attachments.py（API 回归）；frontend/src/api/attachments.ts、frontend/src/components/member/AttachmentsSection.vue 与对应测试。仅更新附件安全 spec 的执行合同。
