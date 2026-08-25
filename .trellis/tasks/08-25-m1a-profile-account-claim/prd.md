# m1a 档案、账号认领与代管权

> 父任务：[08-25-m1-relations-family-space](../08-25-m1-relations-family-space/prd.md)｜依赖：m0b｜身份模型：architecture.md §1 `[AD-1]`

## Goal

建档向导 + PersonProfile/Account/Claim 分离落地：每个亲人一张档案、一份待认领凭据，代管权规则清晰。

## Requirements

- users 表补全档案字段（gender/birth/death/bio/avatar_path/privacy_mode/created_by/claim_status/deleted_at 占位）。
- 建档向导（前端四步）：资料（名字允许重名/性别/生卒/简介）→ 归属模式 D5 二选一 → 是否加入我的空间（默认当前空间）→ 提交。
- 提交行为：创建 user+account，随机 PIN **一次性弹窗展示**（大字号+复制+"请截图保存"提醒），此后任何接口不可再查原始 PIN；audit 记录 created_by。
- 代管权判定服务：perpetual=创建者永久编辑；handover=未 claimed 创建者可编辑、claimed 后创建者只读（403）。
- 本人档案编辑权：claimed 用户可编辑自己全部档案字段。
- 删除档案 API `DELETE /users/{id}` 按 architecture.md §7 `[AD-5]` 实现：权限三分、二次确认（前端输入名字）、单事务级联、文件异步清理、audit 快照保留。（关系边/成员行级联随外键自然生效）

## Acceptance Criteria

- [ ] 为父/母/配偶/子女各建一档，各获一次性 PIN 且刷新后不可回看。
- [ ] handover 档案在被创建人 claimed 后，创建者写接口 403；perpetual 不受限。
- [ ] 非本人且非代管者调用删除 API 403；删除后关系边/成员行/pending 请求消失，audit 保留快照。
- [ ] 重名两人可并存，ID 独立。

## Non-goals

- 关系建立 UI（m1b）；公农历换算细节（m1d 引入基础、m3b 完善）。
