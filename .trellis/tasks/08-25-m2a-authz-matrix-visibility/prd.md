# m2a 授权矩阵与可见性模块

> 父任务：[08-25-m2-clan-view-privacy](../08-25-m2-clan-view-privacy/prd.md)｜依赖：M1 全部｜矩阵契约：architecture.md §6

## Goal

服务端强制的授权单点：`services/visibility.py` + 资源级授权矩阵 + 逐行 IDOR 测试。**本任务是 M2/M3 所有数据出口的前置门禁。**

## Requirements

- 授权判定公式（QU1=B + AD-9 定稿）：完整数据 ⇔ **同空间双方 active ∨ 直系结构边（dir_class ∈ {elder,younger,spouse}）active 对端**；其余家族可达者 → 必要字段（名字/称谓/世代）+ 归属者披露开关已开放的类别（AD-9），未开放类别 MASKED；其余 invisible。pending 期间双方互见摘要。
- 矩阵逐行实现（档案详情/图节点+边/头像原图/附件元数据与下载/搜索命中/统计聚合/join_request 可见/管理 API）。
- 遮罩返回结构：被遮罩字段统一 `{__masked__: true}` 而非省略，前端 MaskedField 据此渲染锁样式。
- 图查询接入：clan scope 下不可达节点不返回；可达但非同空间节点仅摘要字段。
- 文件下载改走授权端点流式返回 + nosniff/Content-Disposition（nginx 直链 uploads 移除）。
- `tests/test_authz_matrix.py`：普通 JWT 直打每个资源×每种主体关系的断言表；fixture 覆盖三类对端——直系边（A—elder→B）、peer 边（B—peer→C）、独立家族（D），并含披露开关开/关两态断言。

## Acceptance Criteria

- [ ] 三家庭 fixture（A—elder→B、B—peer→C、D 独立家族）：A 见 B 完整档案（直系互见）；A 见 C 基线摘要，未开放披露时 photo/birth/death/bio/attachments 均 MASKED。
- [ ] C 的归属者开放 dates/photos 披露后，A 对 C 的对应字段返回 full，其余仍 MASKED；关闭后恢复。
- [ ] 头像原图对 clan 可达者默认占位图，avatar 开放后返回原图；附件下载在未开放 attachments 时 404 语义。
- [ ] D 家族在任何接口/关键词下均不可见（invisible ≠ 遮罩）。
- [ ] IDOR 测试覆盖矩阵每一格且全绿；CI 门禁包含该文件。

## Non-goals

- 家族视图前端呈现（m2b）；申请流 UX（m2c）。
