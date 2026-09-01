# 实现记录

## 缺陷根因（代码确认，非 PRD 原文）

`StructuredDate` 不看 `cal_type` 就用 `date.fromisoformat` 校验 `date`
（`schemas/user.py`），农历因此落库为 ISO；而 `lunar_to_solar` 要 `'YYYY:M:D'`、负月表闰月
（`services/lunar.py`）。`enrich_structured_date` 的农历分支把 ISO 喂给冒号解析器，
农历方向 `mirror_date` 恒 `None`，公历方向正常。

调查中另发现三处未登记事实：

1. `lunar.py` docstring 声称农历存冒号形式，与 schema 直接矛盾。
2. `test_m1d_lunar_positions.py` 断言的是冒号输入——生产链路永远产生不了的形状，
   所以测试长期为绿而功能一直坏。这是缺陷能存活的直接原因。
3. `mirror_date` 不是 `StructuredDate` 字段，模型往返即丢弃，只活在 DB 的 JSON 列里。

## 关键决策

- **闰月用独立布尔键 `is_leap_month`，容器仍是 ISO。** 负月表示被限制在
  `services/lunar.py` 与 lunar-python 之间，不外泄。`abs()` 折叠因此得以删除，
  闰二月十五与平二月十五在存储上首次可区分。
- **`is_leap_month` 恒描述农历那一侧**（`date` 与 `mirror_date` 中的农历者，二者必有且
  仅有一个是农历）。故公历行的农历镜像落在闰月时该键同样为 true，往返不丢信息。
  设计文档 §2.2 初稿写"非农历标闰月即 422"，据此收紧为仅 `cal_type='none'` 才 422。
- **`solar_to_lunar` 签名改为 `tuple[str, bool] | None`**（破坏性，3 处调用点均在本任务
  范围内，mypy strict 兜底）。
- **不做数据迁移**（用户裁定）。历史农历行 `mirror_date` 仍为 `None`，
  由 `canonical_birth` 的现算降级分支覆盖，不会因此判错。原 S6 迁移步骤已删除。

## 顺带修正

- `api/misc.py` 统计原用 `or` 兜底，把农历行当公历解析，农历录入者的生日月份与世代
  分桶是错的；改为读 `mirror_date`。
- `person_identity.canonical_birth` 自带的一套平行换算已删除，改为读 `mirror_date`
  并保留现算降级——此前它违反 architecture §0.9"口径不得在两处各写一套"。
- `MaskedField.vue` 注释写"优先人读文本"但代码从未读 `original_text`；已修正为真正
  优先原文，并补历别与闰月标注。`ProfileDrawer` 同步补闰月标记（此前闰月与平月同形）。

## 规范更新

- `architecture.md`：删除两条"已知缺陷"登记；§0.9 改为"农历行读 `mirror_date`，
  缺失时现算，换算真源是 `services/lunar.py`"。
- `database-guidelines.md`：新增"结构化日期存储约定"节。原先规范里 `StructuredDate`
  完全无记载（缺陷得以长期存活的制度原因），末行那句 `{cal_type,date,original_text}`
  既缺键又指向没有该内容的 architecture，已就地修正。

## 验证

- 后端 623 passed / 3 skipped（预存在 skip），mypy strict 132 文件、ruff、format 全通过。
- 前端 262 passed（42 文件），type-check 与 lint 通过。
- 全量 pytest 按惯例 deselect `test_ownership_transfer.py::test_concurrent_double_accept_single_winner`
  （间歇挂死）；单独跑 0.83s 通过，确认与本次改动无关，仍归
  `09-01-ownership-transfer-test-deadlock`。

## 对其他任务的影响

`09-01-person-identity-dedupe`：其代码已在工作区（staged 新文件、任务仍 planning）。
本任务改了 `canonical_birth` 函数体与 docstring，以及 `test_person_dedupe.py` 中两处
断言"mirror_date 恒 None"的旧描述；未触碰 `match_strength` 与阈值。
其余六个任务无农历引用，不受影响。

未提交：本任务只改工作区，归档与提交留待验收。
