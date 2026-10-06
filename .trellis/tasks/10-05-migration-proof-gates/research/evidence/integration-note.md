# 集成说明：本分支携带 10-03 的业务代码（必须按序合并）

## 事实

`feat/10-05-migration-proof-gates` 相对 `main` 有 30 个 commit：

| 来源 | 数量 | 是否触碰 `backend/` |
|---|---|---|
| 本任务（10-05）自身 commit（`3bf4a069..HEAD`） | 19 | **0** |
| 经 merge `3bf4a069` 带入的 commit | 11 | 其中 4 个触碰 `backend/` |
| （其余为 10-03 的规划/证据 commit） | — | 否 |

带入的 4 个业务 commit：

```
73cfd21e fix(db): keep partial unique indexes partial on PostgreSQL
34eff92e test(db): classify every quota-bearing status transition
72a62995 fix(models): render the JSON snapshot CHECK for PostgreSQL too
071df48d fix(db): make runtime JSON queries portable to PostgreSQL
```

## 为什么这样处理

merge 是**刻意的**：Gate 1 的扫描必须审计「实际会发布的代码」。在 merge 之前，
本 worktree 从 `main` 分支，扫到的是**未修复**的代码（16 个 `sqlite_where` /
0 个 `postgresql_where`），会把已修复的缺陷重新报成待办。见
`gate-1-report.md` 的「审计基线」一节。

## 合并顺序（硬约束）

**不得**把 `feat/10-05-migration-proof-gates` 直接 merge 进 `main`，否则会连带把
10-03 的业务代码一起合入，违反「一次只合一个分支」。

正确顺序：

```text
1. git checkout main
2. git merge feat/10-03-postgres-migration     # 依赖先合
3. git merge feat/10-05-migration-proof-gates  # 此时带入部分已是 no-op
```

理由：10-05 是 10-03 的前置门（proof gates），依赖方向本来就要求 10-03 先落地。

## 未做

- 没有 rebase、reset 或改写历史（AGENTS.md 禁止）。
- 没有把 10-05 的 commit 摘出来单独成分支（会丢失「审计真实代码」这一前提）。


## 计数为现场生成

上述数字由 `git rev-list --count` 现场计算。若与文档不符，**以 git 为准**并更新本文件——
陈旧计数会误导集成判断。
