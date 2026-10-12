# C0：环境与边界冻结

## 基线

- worktree: `/Users/lyston/PycharmProjects/fg-10-06-architecture-completion-orchestration`
- branch: `feat/10-06-architecture-completion-orchestration`
- commit: `17a08dfe149dd0bff49f782c26a2588a4a179a8e`
- base: `main @ 17a08dfe149dd0bff49f782c26a2588a4a179a8e`
- 隔离 PostgreSQL: 一次性容器，PGTEST_DSN 注入，禁止指向开发库/线上

## 扫描器基线（本次 worktree 实跑，exit 0）

| 指标 | 值 |
|---|---|
| app Python | 214 |
| model Python | 35 |
| migration | 59 |
| 表 | 88 |
| 索引 | 112 |
| 约束 | 105 |
| 触发器（实际对象） | 69 |
| 触发器（源码位点） | 14 |
| 事务入口 | 65 |
| 局部唯一索引 | 4 sqlite / 4 pg |
| SQLite-only 函数命中 | 6 |
| PRAGMA | 31 |
| SQLite DDL | 23 |
| BEGIN IMMEDIATE | 3 |
| 锁序违反 | 0 |
| counter 已实现 | False |

## 事务入口形态

```
{
  "command_transaction": 20,
  "immediate_tx": 22,
  "write_transaction": 23
}
```

## 边界（本任务不做）

- 不直接实现业务 API 或家庭域授权；
- 不把 sidecar 变成可访问数据库的服务；
- 不绕过 Provider gateway 或放宽出境/可见性策略；
- 不自动操作线上环境。

## 环境阻塞记录

- 新 worktree 不含 `backend/.venv`（未跟踪目录）：已符号链接到主检出 venv（已 gitignore）。
  后续任何模型进入新 worktree 都必须先做这一步，否则所有扫描器 exit 127。
