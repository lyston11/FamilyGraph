# 执行计划：农历 mirror_date 与闰月表示

## 顺序（自底向上，每步可独立验证）

### S1 换算层 `backend/app/services/lunar.py`
改 `solar_to_lunar` → `tuple[str, bool] | None`、`lunar_to_solar` 加 `is_leap_month` 关键字、
`enrich_structured_date` 透传闰月并写 `mirror_date`；更正顶部 docstring（当前声称农历存冒号形式，是错的）。

验证：`pytest tests/test_m1d_lunar_positions.py -k lunar`（此时预期红——测试仍断言旧冒号形状，S2 一并改）

### S2 换算层测试 `backend/tests/test_m1d_lunar_positions.py`
`test_leap_month_roundtrip` / `test_enrich_and_garbage` 改为断言生产链路真实形状：
农历 ISO + `is_leap_month`。补平月/闰月不混淆用例（闰二月十五→`2023-04-05`，平二月十五→`2023-03-06`）。

验证：`pytest tests/test_m1d_lunar_positions.py`

### S3 schema `backend/app/schemas/user.py`
`StructuredDate` 加 `is_leap_month: bool = False`、`mirror_date: str | None = None`；
`_validate_consistency` 追加"非农历不得为闰月"校验。

验证：`pytest tests/test_members_api.py`（`:25` 已有携带 `mirror_date` 的固件，正好覆盖往返）

### S4 端点与统计 `backend/app/api/misc.py`
`/lunar/mirror` 加 `is_leap_month` 入参、响应带回 `is_leap_month`；
统计 `:71` 农历行改取 `mirror_date`，去掉 `or` 兜底误读。

验证：`pytest tests/ -k "lunar or stat"`

### S5 身份判定 `backend/app/services/person_identity.py`
`canonical_birth` 删内部平行换算，改为消费 `mirror_date`（缺失时回落 `lunar_to_solar`）；
删除 docstring 里"mirror_date 恒为 None"与"闰月按平月"两段限制。
同步 `backend/tests/test_person_dedupe.py:84` 的 docstring 描述。

验证：`pytest tests/test_person_dedupe.py`

### S6 前端
`types/api.ts` 补两字段 → `api/lunar.ts` 传/收 `is_leap_month` →
`MemberCreateWizard.vue` 删冒号解析与 `abs()` 折叠、`form` 加 `birthIsLeap` →
`ProfileDrawer.vue` 闰月前缀 → `MaskedField.vue` 复核兜底。

验证：`npm run lint && npm run type-check && npm run test && npm run build`

### S7 规范同步
`.trellis/spec/architecture.md:170-176`"已知缺陷"段：删除前两条（mirror_date 恒 None、
闰月无法表达），保留 zhconv 与生日未知两条。§0.9 的"农历自行换算而不读 mirror_date"
一句改为"读 mirror_date"。

## 全量门禁

```
cd backend && ruff check . && mypy . && pytest --deselect <所有权移交并发测试>
cd frontend && npm run lint && npm run type-check && npm run test && npm run build
```

全量 pytest 前必须 deselect 所有权移交并发测试，否则会间歇永久阻塞
（09-01-ownership-transfer-test-deadlock）；先定位其精确 nodeid 再跑。

## 回滚点

全程纯代码，无迁移，`git checkout` 即完整回滚。已写入新键的 JSON 行在旧代码下会被
Pydantic 忽略（`StructuredDate` 无这两字段），不报错，故回滚无需数据处理。
S6 前端独立于后端，可单独回滚。

## 完成定义

PRD 八条验收全绿 + 全量门禁通过 + `architecture.md` 与代码口径一致 +
`person_identity.py` 不再有第二套换算实现。
