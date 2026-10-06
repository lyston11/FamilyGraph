# 集成说明：本分支携带 10-03 的业务代码（必须按序合并）

## 事实

每个数字都附带**生成它的确切命令**，避免不同查询口径互相矛盾（上一版本就因此陈旧）。

| 项 | 命令 | 值 |
|---|---|---|
| 分支相对 main 的 commit（含 merge） | `git rev-list --count main..HEAD` | 46 |
| 分支相对 main 的 commit（不含 merge） | `git rev-list --count --no-merges main..HEAD` | 31 |
| 本任务自身 commit | `git rev-list --count 3bf4a069..HEAD` | 35 |
| merge 带入的 commit | `git rev-list --count main..3bf4a069` | 11 |
| 其中触碰 `backend/` | `git rev-list --count main..3bf4a069 -- backend/` | 4 |
| **本任务自身触碰 `backend/`** | `git rev-list --count 3bf4a069..HEAD -- backend/` | **1**（仅新增测试 `tests/test_agent_queue_settle_race.py`，无生产代码改动） |

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

## 本分支新增的非任务目录内容

`scripts/migration-proof/`（15 个扫描器与探针）是**持久**位置，会随本分支进入 main。
这是刻意的：spec 记录的复跑命令必须指向归档后仍存在的路径。它们不修改业务运行代码，
只读取仓库与隔离数据库。

若希望它们不进入 main，应在合并前把 spec 中的命令改为任务内路径并接受归档后失效——
两者不可兼得。
