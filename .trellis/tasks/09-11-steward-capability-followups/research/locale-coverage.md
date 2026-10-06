# Research — locale coverage 表与来源（AC-3，2026-10-06）

## 生成方式

本文件的数字**不手工填写**，由 `backend` 的隔离迁移库现场统计：

```bash
TMP=$(mktemp -d)
DATA_DIR="$TMP" backend/.venv/bin/python -m alembic upgrade head
# 然后 SELECT level, locale, concept_code, term FROM term_entries
```

数据来源：`app/models/term_registry.py::BUILTIN_TERM_SEEDS`（经迁移 `0012_term_registry`、
`0041_term_pack_expansion`、`0050_term_alias_spouse_fix` 落库）。

## 覆盖统计

| level | locale | 条数 |
|---|---|---|
| `system` | （无） | 25 |
| `locale` | `zh-CN` | **112** |
| `locale` | `wu` | **1** |
| **合计** | | **138** |

## zh-CN 按关系类别（112 条）

| 类别 | 条数 |
|---|---|
| 上行/长辈（`U*`） | 56 |
| 同辈/兄弟姐妹（`B*` / 含 `-B`） | 31 |
| 下行/晚辈（`D*`） | 27 |
| 配偶（`S*`） | 15 |
| 收养（含 `a`） | 4 |
| 继亲（含 `s`） | 4 |
| 伴侣（`P*`） | 3 |

**编码段数分布**：1 段 25 条、2 段 45 条、3 段 34 条、4 段 8 条；最长码 `Um-Um-Um-Um`（4 代上行）。

覆盖了 `relationship_resolver` 编码合同的全部基础类别：`Um/Uf`（父母）、`Dm/Df`（子女）、
`Bm/Bf`（兄弟姐妹）、`Sm/Sf`（配偶）、`Pm`（伴侣），以及 `Uam/Usf` 形态的收养与继亲。

## wu 包：**1 条**，不能宣称方言覆盖

```
Um -> 阿爷
```

单条演示条目。**结论**：wu（吴语）目前**不构成可用方言包**，不得对外宣称地区覆盖。
新增 locale 的前置条件（本任务 R3 既定边界）：

1. 来源可追溯的词表（不是模型生成）；
2. 至少覆盖 parent / child / spouse / in-law / generation 五类的测试集；
3. 同义词选择规则（歧义时如何选）；
4. 未知码的 fallback 行为；
5. 个人/空间纠正优先级回归（`personal > space > locale > system` 不得被新包破坏）；
6. 未成年人/敏感称谓的内容审核。

## 未知词降级行为（AC-3「未知词可解释降级」）—— 已实测

解析优先级：`personal > space > locale > system`。实测（隔离迁移库，真实调用
`resolve_term_or_structural`）：

| 输入码 | 结果 | `source_level` |
|---|---|---|
| `Um` | `爸爸` | `locale`（zh-CN 命中） |
| `Um-Um-Um-Um` | `高祖父` | `locale`（4 代上行已覆盖） |
| `Um-Um-Um-Um-Um` | `高祖父的父亲` | **`derived`**（无词典条目，结构化推导） |
| `Dm-Dm-Dm-Dm-Dm` | `玄孙的儿子` | **`derived`** |

**关键结论**：词典未覆盖的合法码**不会**返回空、也不会伪造称谓，而是降级为
`source_level='derived'` 的**结构化描述**（由 `relationship_resolver` 的编码规则推导）。
`entry_id` 为 `None` 表明它不是词典条目——消费方可据此区分「词典命中」与「推导」。

另有一条格式边界（实测）：格式非法的码（如 `Zz-Zz-Zz-Zz`，小写段不合法）返回
**422 `CONCEPT_CODE_INVALID`**，在词典查找之前就被拒绝。即：**格式非法** 与
**格式合法但未覆盖** 是两条不同路径，前者显式报错，后者可解释降级。

## 不把模型生成词升为全局标准

`design.md` 与 `research.md` 均已明确：模型生成的词只能进入**个人候选**并经用户确认，
**不得**直接写入 `locale`/`system`。本次统计中没有任何模型来源条目——138 条全部来自
`BUILTIN_TERM_SEEDS` 的静态清单。

## 未覆盖项（诚实声明）

1. ~~未知码降级路径未实测~~ → **已实测**（见上表，降级为 `derived` 结构化描述）。
2. wu 以外的方言（粤语、闽南语等）完全未评估。
3. 「两人共用空间别名晋升」门槛（`≥2` 个 identity_confirmed 用户）未在本轮验证。
4. 敏感/未成年人称谓审核清单未建立。
