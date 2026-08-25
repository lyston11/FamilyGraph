# M1 建档、四分类关系与家庭空间三布局

> 权威上下文：[.trellis/HANDOFF.md](../../HANDOFF.md)。已确认决策视为锁定。

## Goal

实现产品核心闭环：为亲人建档（产生可登录账号）、建立四分类关系、以三种布局呈现家庭空间。出口标准：**能把父母/子女/配偶建出来并正确显示世代结构**。

## Background（锁定决策引用）

- A3：添加关系 ≈ 创建账号，随机 6 位 PIN 仅展示一次。
- D1：单一全局图；家庭空间 = 显式成员集合视图。
- D2：关系 = 方向 + 结构四分类（长辈/晚辈/平辈/配偶）+ 自由称谓标签；四分类唯一决定树形骨架。
- D4：新建账号直接连；拉已有账号需对方确认（pending 状态）。
- D5：档案归属创建时二选一：① 创建者永久可编辑 ② 本人登录后创建者退为只读；未登录由创建者代管。
- D6：一人可属多个空间、可建多个空间。
- U1/U2：第一人称渲染；三布局 = 画布拖拽（位置记忆）/ 树状 / 列表（长幼排序）。
- D7：公农历双录任一自动互转（本阶段完成录入与换算基础）。

## Requirements

### 数据模型
```
family_spaces   id, name, owner_id, created_at
space_members   space_id, user_id, added_by, role(owner|member), status(pending|active), created_at
relations       id, from_user, to_user, dir_class(elder|younger|peer|spouse),
                label(自由文本，可空), created_by, status(pending|active), created_at
users 扩展      gender, birth{cal_type: solar|lunar, date, original_text},
                death{同构}, bio, avatar_path, privacy_mode(perpetual|handover),
                has_logged_in, created_by, died_at 无需单独字段
node_positions  user_id, space_id, x, y（画布手动位置记忆）
```
- 关系写入校验：`elder/younger` 边做环检测（拒绝成环）；`spouse` 边不参与层级计算；方向语义 = `from_user 视角`描述 `to_user`。
- 农历换算：lunar-python 集成，录入任一历自动补另一历；闰月正确往返；保留 `original_text` 原文备注。

### 建档向导（添加成员）
1. 填资料：名字（允许重名）、性别、生卒（历别选择 + 自动互补 + 原文备注）、简介。
2. 归属模式选择：① 创建者永久可编辑 ② 本人登录后创建者退为只读。
3. 是否同时加入我的某个家庭空间（默认加入当前空间）。
4. 提交 → 系统生成随机 PIN **一次性弹窗展示**（含"复制"按钮与保存提醒），此后不可再查。

### 关系与邀请
- 对**新建**账号：建档即按所选结构分类直连（status=active）。
- 对**已有**账号：搜索选择 → 发起连接（dir_class + label）→ 对方登录后确认才 active；同时发起空间拉入请求（pending）。
- 收到的请求在"我的设置 → 连接请求"列表处理（接受/拒绝）。

### 家庭空间页面（默认首页）
- 卡片画布（Vue Flow），成员卡片 = 头像/名字/称谓标签/世代角标；点击卡片打开档案抽屉。
- 三种布局一键切换：
  - 🎨 画布拖拽：自由摆放，位置持久化到 node_positions；新成员自动落位。
  - 🌳 树状：d3-hierarchy 按 elder/younger/spouse 骨架计算层级；布局失败（数据异常）回退画布模式并提示。
  - 📋 列表：按长幼排序（生日升序，缺生日按创建时间——开放问题 #1 默认方案），平辈内同样规则。
- 空间管理：创建多个空间、切换空间、移除成员（不动档案，D8 断连轨）。
- 档案页/抽屉：详情查看 + 按权限编辑（D5 双模式判定逻辑）；本人可编辑自己。

## Acceptance Criteria

- [ ] 完整旅程：为父亲、母亲、配偶、子女各建一个账号，各获得一次性 PIN；四人凭名字+PIN 能登录并看到以自己为中心的家庭空间。
- [ ] 树状布局中长辈在上、晚辈在下、配偶同级并列，世代层数正确。
- [ ] 再婚场景：同一人两条 spouse 边时树状布局不报错、不产生错误层级。
- [ ] 故意构造 elder 成环的关系写入被 API 拒绝并返回明确错误。
- [ ] 拉已有账号进入空间产生 pending 记录；对方接受前不出现在其空间活跃成员中，接受后出现。
- [ ] privacy_mode=handover 的档案在被创建人登录后，创建者编辑接口返回 403。
- [ ] 画布拖动刷新后位置保留；三种布局切换流畅无报错。
- [ ] 公历输入"2024-01-01"自动生成对应农历并支持闰月日期往返一致。

## Non-goals

- 家族空间视图与隐私遮罩（M2）。
- 图片/链接附件管理界面（M3；仅保留 users.avatar_path 字段）。
- 统计页、全局搜索（M3）。
- 过渡动画打磨（M4）。

## Open Questions

- #1 列表长幼排序兜底：默认生日缺失按创建时间，实现时如发现更优信号（如辈分+姓名字辈）可在 PRD 修订记录中调整。
