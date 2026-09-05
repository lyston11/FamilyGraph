# 家庭端导航/资料编辑/披露策略修复与种子补全 Technical Design

## 1. 上下文感知返回（R1）

- vue-router history state 携来源：进入方 `router.push({ name: 'person-profile', params, state: { fgBackTo: 'home' | 'family-space' } })`
  （HouseholdCardView.openMember → 'home'；FamilyTreeView.onNodeSelect → 'family-space'）；
- PersonProfileView 返回按钮：`history.state?.fgBackTo ?? 'home'`，标签随值切换
  （「返回家庭卡」/「返回家族树」）；刷新丢失 state 时兜底 home（符合 PRD）；
- SettingsView：返回按钮改为 `router.push({ name: 'home' })`，标签「← 返回我的家庭」；
- 不用 history.back()（可能退出站外），不用 query（避免污染 URL/分享链接）。

## 2. 基础资料编辑（R2）

- SettingsView 个人资料区新增 NForm：性别 NRadioGroup(f/m/unknown)、出生/逝世
  NDatePicker（StructuredDate {year,month,day} ⇄ ISO 串 ⇄ null 转换，空值 null 红线）、
  简介 NInput textarea（maxlength 2000 + 字数提示）；
- 提交：`PATCH /members/{auth.user.id}`（api 层若无此函数则在 frontend/src/api 新增
  updateMemberProfile，body 仅含变更字段，extra=forbid）；成功后刷新 auth store 回显；
- 后端零改动（MemberUpdateRequest 已含 name/gender/birth/death/bio）；
- 既有 data-test 契约（name-save 等）保持。

## 3. 披露合同放开（R4/R5）

- backend/app/services/disclosure.py：删除"高敏感不落行"分支——五类高敏感与基础类
  同一 PUT 路径落行；新增守卫：`visibility.is_minor(target)` 时对 high-risk 类别
  raise 422（DISCLOSURE_MINOR_FORBIDDEN，新错误码，防枚举文案不泄露年龄）；
- 审计：PUT /members/{id}/disclosure 命中高敏感类别且值变化时写 audit
  （action='disclosure_high_risk_changed'，detail 记类别与目标值，不含内容）；
- 前端 DisclosureMatrix：HIGH_RISK_HINT 改为"默认关闭，本人可开启（二次确认）"；
  高敏感开关 enabled（is_minor 时仍禁用+提示），点击未开启的高敏感开关先弹 NModal
  强确认（列类别名+后果+可撤销说明），confirm 后写草稿；关闭操作不弹确认；
- visibility.py 不改：披露矩阵放行后，可见性按 disclosed_categories 自然生效
  （高敏感内容列当前为空占位，无可见性变化——诚实告知用户）。

## 4. 种子补全（R3/R6）

- dev_seed：新增 lineage 空间「王氏家族」；6 用户补 birth（1940/1965/1967/1990/1993/2018）；
  6 人挂 lineage 空间（王德海 space_admin 其余 member）+ household 空间维持现状；
- 披露默认：6 人基础五类全局 open（经 disclosure service 正常路径写入，高敏感不写）；
- PFV：household 与 lineage 投影均为按需计算（实测种子后 status=current），
  种子不触发额外计算；若实现时发现 lineage 需异步触发，补最小触发调用并注明；
- test_dev_seed.py 更新：users=6（含 birth 断言）、spaces=2、成员行=12、
  disclosure 基础类全开断言、高敏感全关断言。

## 5. 测试与守卫

- frontend：PersonProfileView 返回行为（state 有/无两路径）、SettingsView 返回目标 +
  表单保存回显、DisclosureMatrix 高敏感确认弹窗（n-modal document 查询模式）；
- backend：disclosure 放开（成年可开/未成年 422/审计落行）、seed 更新；
- 门禁：backend ruff/mypy/pytest；frontend lint/type-check/test/build。
