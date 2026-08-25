# m2a 授权矩阵与可见性模块

> 父任务：[08-25-m2-clan-view-privacy](../08-25-m2-clan-view-privacy/prd.md)｜依赖：M1 全部｜矩阵契约：architecture.md §6

## Goal

服务端强制的授权单点：`services/visibility.py` + 资源级授权矩阵 + 逐行 IDOR 测试。**本任务是 M2/M3 所有数据出口的前置门禁。**

## Requirements

- 授权判定公式（严格锁定 U5 版，待裁定 QU1）：完整数据 ⇔ **双方在同一空间且均为 active 成员**；active 关系仅授予 clan 连通可达 + 摘要；其余 invisible。若 QU1 裁定为修订版（直系结构边升级），本行与矩阵第二列同步改写并在 HANDOFF 登记。
- 矩阵逐行实现（档案详情/图节点+边/头像原图/附件元数据与下载/搜索命中/统计聚合/join_request 可见/管理 API）。
- 遮罩返回结构：被遮罩字段统一 `{__masked__: true}` 而非省略，前端 MaskedField 据此渲染锁样式。
- 图查询接入：clan scope 下不可达节点不返回；可达但非同空间节点仅摘要字段。
- 文件下载改走授权端点流式返回 + nosniff/Content-Disposition（nginx 直链 uploads 移除）。
- `tests/test_authz_matrix.py`：普通 JWT 直打每个资源×每种主体关系的断言表。

## Acceptance Criteria

- [ ] 三家庭 fixture（A—B—C 连通、D 独立）：A 见 C 摘要、D 全接口不可见。
- [ ] A 直打 C 的档案 API：photo/birth/death/bio/attachments 均为 MASKED 结构。
- [ ] 头像原图 URL 对 clan 可达者返回占位图；附件下载对非同空间 404 语义。
- [ ] IDOR 测试覆盖矩阵每一格且全绿；CI 门禁包含该文件。

## Non-goals

- 家族视图前端呈现（m2b）；申请流 UX（m2c）。
