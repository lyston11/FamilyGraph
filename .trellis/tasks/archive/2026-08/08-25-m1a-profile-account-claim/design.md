# m1a 技术设计

> 遵守 [architecture.md](../../spec/architecture.md) §1（身份模型）、§7（删除级联）。前置：m0b 已归档（认证基座可用）。

## 数据契约（Alembic 0003）

users 增量列（禁止改写已有迁移）：

```
gender            VARCHAR CHECK IN ('m','f','unknown') DEFAULT 'unknown'
birth             JSON NULL   -- {"cal_type":"solar|lunar|none","date":"YYYY-MM-DD|null","original_text":str}
death             JSON NULL   -- 同构
bio               TEXT NULL
avatar_path       VARCHAR NULL        -- m3a 启用上传，本任务仅列
privacy_mode      VARCHAR CHECK IN ('perpetual','handover') NOT NULL DEFAULT 'handover'
created_by        INTEGER NULL REFERENCES users(id) ON DELETE SET NULL
claim_status      VARCHAR CHECK IN ('managed','claimed') NOT NULL DEFAULT 'managed'
deleted_at        DATETIME NULL               -- v1 硬删除，列为审计查询预留，不启用软删路径
clan_disclosure_json JSON NOT NULL DEFAULT '{"avatar":false,"photos":false,"dates":false,"bio":false,"attachments":false}'
```

- **ClaimState 接线**：复用 m0b 的首登强制改 PIN 流——`PUT /api/me/pin` 成功且 `pin_must_change` 由 true 翻转时，同事务置 `claim_status='claimed'`。这是 managed→claimed 的唯一转换点。
- bootstrap 创建的管理员直接 `claimed=true`（创建者即本人）。

## 权限矩阵（custody 服务单点 `services/custody.py`）

签名：`resolve_relation(actor, target) -> {view: full|summary|none, edit: bool, delete: bool}`

| 主体 \ 权利 | view | edit | delete |
|---|---|---|---|
| 本人（claimed） | full | ✔（含 disclosure） | ✔ |
| 代管创建者·handover 未 claimed | full | ✔（含 disclosure） | ✔ |
| 代管创建者·handover 已 claimed | full | ✘（403 CUSTODY_HANDOVER_DONE） | ✘ |
| 创建者·perpetual | full | ✔ 永久 | ✔ |
| admin | full | ✔（audit 记录） | ✔ |
| 其他已登录用户 | none（404 语义，防枚举） | ✘ | ✘ |

> M1 阶段无可见性模块，非相关者一律 none；M2 由 visibility.py 接管 summary 层。

- **编辑权统一入口** `assert_can_edit(actor,target)`：PATCH 档案、disclosure、（将来）附件共用；admin 操作走同一函数但 audit 强制记录。
- disclosure 修改权 = 编辑权（AD-9）。

## API 契约

```
POST   /api/users                    建档（name 必填+gender/birth/death/bio/privacy_mode）
       → 201 {user, pin:"一次性明文"}  ；audit(create_profile, detail 不含 pin 明文)
GET    /api/users/{id}               按 resolve_relation.view 返回；none→404
PATCH  /api/users/{id}               档案字段编辑（edit 权）
PUT    /api/users/{id}/disclosure    五类开关整体替换（校验键集合恰好）
DELETE /api/users/{id}?confirm_name= 单事务级联（当前可级联对象：accounts/audit 外全部 FK CASCADE；
                                     audit_log.target_id 改存快照文本）；异步物理清理占位；
                                     token_version 处理：目标会话随账号行删除自然失效
```

- 删除的**二次确认在前端**（输入名字），后端以 `confirm_name == target.name` 作第二道校验，不符 409 CONFIRM_NAME_MISMATCH。
- PIN 一次性：响应返回后任何接口不可再取；日志/审计只记 `pin_set_at` 不记值。

## 建档向导（前端）

三步向导 + 结果弹窗（**空间勾选步骤留待 m1c 接入**，组件预留第四步插槽）：
1. 资料：名字（必填）/性别/生卒（历别切换占位，m1d 接换算）/简介
2. 归属模式：perpetual | handover 单选（附一句人话解释）
3. 提交 → OneTimePinDialog：大字号 PIN + 复制按钮 + 「请截图保存，关闭后无法再查看」

档案呈现：HomeView 临时改为「与我相关的档案」列表（我创建的 + 我自己 + admin 可见全部），点击开 ProfileDrawer（查看/按权编辑/披露开关组/删除）。M1d 后该列表被画布取代。

## 兼容与回滚

- 0003 为纯增量迁移，downgrade 删列即可回滚。
- m0b 的 candidates 缺 `created_by_name` —— 本任务 users 有 created_by 后补齐 challenge 候选提示字段（小改，纳入验收）。
