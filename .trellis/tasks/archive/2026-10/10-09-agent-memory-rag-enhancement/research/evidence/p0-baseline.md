# P0 检索质量基线（2026-10-09）

完整报告（含逐用例明细）：`artifacts/memory-eval/baseline.json`（本地产物，与
`artifacts/migration-proof` 同一约定，`.gitignore` 已忽略，不入库）。复跑：

```bash
cd backend && .venv/bin/python -m pytest tests/test_memory_eval_baseline.py -q
```

- evaluator: `memory-eval-v1`；fixture: v1（k=5）
- model: `no-model(retrieval-only)` —— **不调用任何模型**，只评测检索层与确定性提取器

## 总体

| 指标 | 值 |
|---|---|
| 通过 | 14/15 |
| retrieval recall@k | 0.955 |
| citation precision | 0.543（命中里属于期望来源的比例） |
| **forbidden hits** | **0** ← 取代语义的直接证伪指标 |
| abstention accuracy | 1.000 |
| latency p50 / p95 | 3.2 / 7.0 ms |

## 分层（本任务最重要的结论）

| 层 | 用例 | 通过 | pass_rate | recall | forbidden |
|---|---|---|---|---|---|
| contract | 12 | 12 | 1.00 | 1.00 | 0 |
| quality | 3 | 2 | 0.67 | 0.83 | 0 |

**contract 层 100% 通过**：取代/有效区间语义、弃答、单跳提取与时序都成立。

**quality 层 2/3**：多会话聚合（一个问题要同时召回两条记忆）当前词法路径做不到。
失败用例：

- `multi-session-story`（quality）：期望 ['story-老宅', 'story-桂花']，实际 ['res-苏州', 'trad-春节', 'trad-端午', 'hist-迁居', 'story-老宅']，缺 ['story-桂花']

这不是 fixture 需要放宽的问题：一个问题同时指向两条记忆本身就是**多会话聚合**，
正是 P3 确定性重排要解决的场景（当前 `MAX_TERMS=8` 的单轮词法把第二条挤出了 top-k）。
因此记录为基线，由 P3 提升后升级为 contract 层。

## 按能力维度（LongMemEval 定义的五类）

| 能力 | 用例 | 通过 | mean_recall |
|---|---|---|---|
| 知识更新 | 3 | 3 | 1.00 |
| 信息提取 | 3 | 3 | 1.00 |
| 时序推理 | 3 | 3 | 1.00 |
| 多会话推理 | 3 | 2 | 0.83 |
| 弃答 | 3 | 3 | 1.00 |

## 提取器（确定性规则，零模型调用）

- contract 层：8/8 = 1.00
- 已知能力缺口（记录为 P2 输入，**不放宽 fixture**）：
  - `x-birthday`：期望 ['birthday']，实际 [] —— `_BIRTHDAY_DATE` 只认阿拉伯数字（`3月5日`），中文数字日期（`三月五日`）不命中
  - `x-anniversary`：期望 ['anniversary']，实际 [] —— 同 birthday：中文数字日期不命中
  - `x-determinism`：期望 ['occupation']，实际 ['occupation', 'school'] —— `在南京的中学教书` 同时命中 occupation 与 school（school 规则只看`在…中学`，不看谓语是『教书/工作』还是『在读』）

## 本次评估顺带发现并修掉的两个真实缺陷

1. **别名表缺反向项**：查询说「春节」而正文写「过年」时不召回
   （`ALIAS_TABLE_V1` 此前只有 `过年→春节`）。已补 `春节→过年`、`聚餐→聚会`。
2. **`expect_empty` 被误用为唯一表达**：原 `expired-not-current` 用例想要的是
   「问『现在』时不得把已结束的事实当当前事实」，却写成期望整条返回空——而问题里的
   「舅舅」本来就合法匹配其它记忆。评测器因此引入三个互不混同的 `mode`：
   `answerable` / `abstention` / `forbidden_only`。

