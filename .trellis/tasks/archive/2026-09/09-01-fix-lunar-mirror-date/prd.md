# 修复农历日期 mirror_date 生成

## Goal

让农历录入的生卒日期能正确产出 `mirror_date`（当前恒为 `None`），并让闰月在数据模型里可表达。

农历与公历共用一个 `YYYY-MM-DD` 容器，是本缺陷的根因；本任务把闰月标记显式化，
使"同一天的农历录入与公历录入"在存储、统计、身份判定三处口径一致。

## 背景：两套互不兼容的表示

- `StructuredDate` 无视 `cal_type`，一律用 `date.fromisoformat` 校验 `date`
  （`backend/app/schemas/user.py:40-43`），故农历落库形如 `1948-03-12`，数字含义是农历年月日。
- `lunar_to_solar` 期望 `'YYYY:M:D'`，月份为负表示闰月（`backend/app/services/lunar.py:29-38`）。
- `enrich_structured_date` 的农历分支（`lunar.py:48`）把 ISO 串喂给冒号解析器 → 恒 `None`。

已在 venv 复现：

```
lunar  1948-03-12 -> mirror_date = None
solar  1948-04-20 -> mirror_date = '1948:3:12'   # 反方向正常
lunar_to_solar('1948:3:12') -> '1948-04-20'      # 函数本身正确，API 喂不进该格式
```

派生问题：

- `lunar.py:7-9` docstring 声称农历存冒号形式，与 schema 矛盾。
- `tests/test_m1d_lunar_positions.py:19-21` 断言的冒号输入是生产链路产生不了的形状，
  故测试长绿而功能长坏。
- `misc.py:71` 统计把农历行当公历解析 → 农历录入者的生日月份与世代分桶错误。
- `MaskedField.vue:29-30` 在 `date` 缺失时会把 `1948:3:12` 原样显示给用户。
- `mirror_date` 不是 `StructuredDate` 字段，模型往返即被丢弃（已验证）；
  它只存在于 DB 的 JSON 列与手工构造的 dict 中。

## Requirements

- 农历 `date` 按农历 `Y-M-D` 解读并换算出公历 `mirror_date`；公历方向保持现有行为。
- 闰月显式表达：`StructuredDate` 增加闰月标记键，不再靠负数月份或 `abs()` 折叠。
  - `cal_type != "lunar"` 时该键必须为假值，schema 层拒绝矛盾输入。
- `mirror_date` 成为 `StructuredDate` 的正式字段，停止在模型往返中被静默丢弃。
- **不做迁移回填**（用户裁定）：JSON 列形状向后兼容，历史行的 `is_leap_month` 缺省为
  `False`、`mirror_date` 保持 `None`，下次写入该档案时由 `enrich_structured_date` 自然补齐。
  读取侧必须容忍缺键——`canonical_birth` 在 `mirror_date` 缺失时现算换算。
- 统计口径修正：`misc.py` 对农历行取 `mirror_date` 的公历月份，不再误读 `date`。
- `person_identity.canonical_birth` 改为消费 `mirror_date`，删除其内部平行换算，
  满足 architecture §0.9"判定口径不得在两处各写一套"。
- 前端 `types/api.ts`、`MemberCreateWizard.vue`、`MaskedField.vue`、`ProfileDrawer.vue`
  跟随新表示；去掉 wizard 里的 `abs()` 闰月折叠。
- 更正 `lunar.py` docstring 与 `architecture.md` 的"已知缺陷"段。

## Acceptance Criteria

- [ ] 农历 `1948-03-12` 与公历 `1948-04-20` 互为 `mirror_date`，双向往返稳定。
- [ ] 闰月：农历闰二月十五 ↔ 公历 `2023-04-05` 往返正确，且与平月二月十五
      （`2023-03-06`）不再互相混淆。
- [ ] `StructuredDate` 往返保留 `mirror_date` 与闰月键；`cal_type` 与闰月键矛盾时 422。
- [ ] 历史行（无 `is_leap_month` / `mirror_date` 键）读取不报错，身份判定仍正确。
- [ ] 农历录入者出现在正确的生日月份与世代分桶中。
- [ ] `canonical_birth` 不再自行换算，跨历同一天仍判 `strong`；
      `test_person_dedupe.py` 全绿。
- [ ] `tests/test_m1d_lunar_positions.py` 改为断言生产链路的真实形状。
- [ ] 后端 `pytest` / `ruff` / `mypy` 与前端 `lint` / `type-check` / `test` / `build` 全绿。

## Notes

- 跑全量 pytest 前先 deselect `所有权移交并发测试`，它会间歇永久阻塞
  （见 09-01-ownership-transfer-test-deadlock）。
- 与 09-01-person-identity-dedupe 有共享面：`person_identity.py` 及其测试是 staged
  新文件，其 docstring 把"mirror_date 恒为 None"写成既定事实，本任务修复后作废；
  `architecture.md:170-176` 的"已知缺陷"段为两任务共用，改动需两边口径一致。
- 其余六个活跃任务无农历引用，不受影响。
