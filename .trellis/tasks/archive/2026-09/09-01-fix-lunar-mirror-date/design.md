# 设计：农历 mirror_date 与闰月表示

## 1. 表示决策

保留 `YYYY-MM-DD` 作为农历容器（农历年-月-日，月份恒为 1..12 正数），闰月由**独立布尔键**承载：

```json
{"cal_type": "lunar", "date": "2023-02-15", "is_leap_month": true,
 "mirror_date": "2023-04-05", "original_text": "闰二月十五"}
```

理由：

- ISO 容器不变 → 不动 DB 列类型，无需重建表；`date.fromisoformat` 校验仍可复用。
- 负数月份（lunar-python 的内部约定）不外泄到存储与前端，前端 `abs()` 折叠可删除。
- 平月二月十五与闰二月十五在存储上可区分，这是当前模型做不到的。

**边界口径**：负数月份只存在于 `services/lunar.py` 内部与 lunar-python 之间。
`lunar.py` 是唯一翻译层，向上只暴露 `(iso_date, is_leap_month)` 对。

`is_leap_month` 为 `cal_type != "lunar"` 时必须是 `False`/缺省，schema 层校验矛盾输入。

## 2. 层次改动

### 2.1 `app/services/lunar.py`（唯一换算出口）

签名调整，闰月进出都显式：

```python
def solar_to_lunar(date_iso: str) -> tuple[str, bool] | None   # (农历 ISO, 是否闰月)
def lunar_to_solar(date_iso: str, *, is_leap_month: bool = False) -> str | None
```

- `solar_to_lunar`：取 `lunar.getMonth()`，负数 → `(abs(m), True)`，输出 `YYYY-MM-DD`。
- `lunar_to_solar`：入参是农历 ISO，内部拆成 `y/m/d`，`is_leap_month` 时传 `-m` 给
  `Lunar.fromYmd`。不再解析冒号串。
- `enrich_structured_date`：按 `cal_type` 分派，写回 `mirror_date`；农历行同时透传
  `is_leap_month`。失败仍置 `None` 不抛。

已验证的库行为：`Solar.fromYmd(2023,4,5).getLunar()` → 月份 `-2`；
`Lunar.fromYmd(2023,-2,15)` → `2023-04-05`；`Lunar.fromYmd(2023,2,15)` → `2023-03-06`。

**破坏性**：`solar_to_lunar` 返回值由 `str|None` 变为 `tuple|None`。调用点仅
`misc.py:35`（`/lunar/mirror` 端点）与两个测试文件，全部在本任务内改完。

### 2.2 `app/schemas/user.py`

`StructuredDate` 增加两个字段：

```python
is_leap_month: bool = False
mirror_date: str | None = None
```

`is_leap_month` 的归属口径：**恒描述农历那一侧** —— `cal_type="lunar"` 时指 `date`，
`cal_type="solar"` 时指 `mirror_date`。`date`/`mirror_date` 有且仅有一个是农历，故无歧义。
公历录入的农历镜像可以合法落在闰月（如 `2023-04-05` → 闰二月十五），所以公历行带
`is_leap_month=True` 是正确状态，不可拒绝；这样公历侧也保住闰月信息，往返不丢。

`_validate_consistency` 追加：仅 `cal_type == "none"` 且 `is_leap_month` 为真 → `ValueError`
（→ 422）。`mirror_date` 由服务端 `enrich` 覆写，请求携带的值不可信，写入路径一律重算。

`mirror_date` 成为正式字段后，模型往返不再丢弃它——这是 `MaskedField.vue` 能拿到
`mirror_date` 的前提（当前拿不到）。

### 2.3 `/lunar/mirror` 端点（`app/api/misc.py`）

请求增加 `is_leap_month: bool = False`；响应由 `{"mirror": str|None}` 扩展为
`{"mirror": str|None, "is_leap_month": bool}`，让前端切到农历时知道落在闰月。
公历→农历方向的 `is_leap_month` 来自换算结果；农历→公历方向回显入参。

### 2.4 统计（`app/api/misc.py:71`）

农历行改取 `mirror_date` 的公历月份/年份；公历行仍取 `date`。
`date_str = birth.get("mirror_date") if cal_type == "lunar" else birth.get("date")`，
两者皆缺则跳过。现有 `or` 兜底会把农历 `date` 当公历用，必须去掉。

### 2.5 `app/services/person_identity.py`

`canonical_birth` 删除内部平行换算（`:88-98`），改为：公历取 `date`，
农历取 `mirror_date`；缺失时回落调用 `lunar_to_solar(date, is_leap_month=...)`
（应对尚未 enrich 的历史行），不再自己拆串。docstring 里"mirror_date 恒为 None"
与闰月按平月换算两段限制描述随之删除。

比对键因此天然含闰月：闰二月十五与平二月十五换算出不同公历日 → 不再误判 `strong`。

### 2.6 前端

- `types/api.ts`：`StructuredDate` 补 `is_leap_month?: boolean`、`mirror_date?: string | null`。
- `api/lunar.ts`：`fetchLunarMirror` 传 `is_leap_month`，返回 `{mirror, is_leap_month}`。
- `MemberCreateWizard.vue:142-158`：删除冒号解析与 `abs()` 折叠（`:150-153`），
  直接用响应的 ISO 与 `is_leap_month` 预填；`form` 增加 `birthIsLeap`。
- `ProfileDrawer.vue:178-188`：`calPrefix` 在闰月时显示"农历闰"。
- `MaskedField.vue:29-30`：`date ?? mirror_date` 的兜底保留（现在 `mirror_date` 真能拿到，
  且已是 ISO 公历，不会再显示冒号串）。

## 3. 不做迁移（用户裁定）

`birth`/`death` 是 JSON 列，新增键无 DDL，形状向后兼容，因此不写迁移、不回填历史行：

- 历史行缺 `is_leap_month` → Pydantic 缺省 `False`（等同此前的"闰月按平月"行为，无回退）。
- 历史行 `mirror_date` 仍为 `None`（农历行）或冒号串（公历行，此前 `enrich` 写入的旧格式）。
- 下次写入该档案时，`enrich_structured_date` 自然把两键补成新口径。

**读取侧必须容忍缺键与旧格式**，这是不迁移的代价，落在两处：

- `canonical_birth`：`mirror_date` 缺失或非 ISO 时回落现算 `lunar_to_solar`，
  故历史农历行的身份判定仍正确。
- 统计：农历行 `mirror_date` 为 `None` 时跳过该行（不再像现在那样误当公历解析）——
  历史农历行在统计中从"错误分桶"变为"不计入"，这是正确性上的改善。
- `MaskedField.vue` 的 `date ?? mirror_date` 兜底对公历历史行可能拿到冒号串，
  但 `date` 恒存在（schema 必填），该分支实际不可达。

## 4. 风险

| 风险 | 缓解 |
|---|---|
| `solar_to_lunar` 签名变更漏改调用点 | 调用点仅 3 处，mypy strict 会全部报出 |
| 历史闰月数据无从恢复 | 既有信息缺失，非本次引入；`original_text` 保留录入原文可人工校正 |
| 不迁移 → 读取侧需容忍缺键与旧格式 | `canonical_birth` 回落现算；统计对 `mirror_date` 缺失的农历行跳过而非误读 |
| 与 person-identity-dedupe 的 staged 代码冲突 | 只改 `canonical_birth` 函数体与 docstring，不动 `match_strength` 与阈值 |

## 5. 不在范围

- 农历年份的干支/生肖展示、人读文本渲染（前端既有 lunar 库职责）。
- `original_text` 的解析回填（人工校正通道即可）。
- 统计中农历生日"按农历月份"聚合的产品语义变更——本任务只修正现有公历口径的正确性。
