# C0–C8 交付报告（2026-10-06）

## 交付概览

| Gate | 状态 | 关键交付 |
|---|---|---|
| C0 | **done** | 环境/边界冻结、扫描器基线、依赖矩阵 |
| C1 | **done** | PG baseline：87 表 + **66 触发器等价物** + 13 双向用例 |
| C2 | **done** | 持久化 capacity counter + 3 个租约入口 + 8 处归还路径（行级门） |
| C3 | **partial** | 集群级执行名额（**反证**进程内 limiter 翻倍）；AC-5 分进程未做 |
| C4 | **partial** | 流级三层名额 + 流级墙钟上限；circuit/backpressure 未做 |
| C5 | **partial** | Redis 降级层（三态、冷却、绝不 fail-open）；未接入准入路径 |
| C6 | **partial** | 词法方言分派（PGroonga 接入）；pgvector union/rerank 未做 |
| C7 | **partial** | writer epoch + migration health + 全部写路径守卫；PITR/HA 未做 |
| C8 | **partial** | 三层配额守恒 + 归还守恒（含双重变异）；真实负载未做 |
| C9 | **blocked** | 需真实开发环境部署（停止条件） |
| C10 | **blocked** | 需真实历史库与多实例（停止条件） |

## 实测发现（改变设计的部分）

1. **`SKIP LOCKED` 不保证配额**：每租户上限 2 被放成 **5**。必须用持久化 counter。
2. **进程内 limiter 不保证集群配额**：两实例各配 2，集群实际 **4**。需要第二层。
3. **`json` 类型没有相等运算符**：12 个 sri 触发器 + 1 个证据守卫在 PostgreSQL 上
   运行期 `UndefinedFunction`。SQLite 的 JSON 是 TEXT，**永远测不出**。
4. **`create_all` 看不到触发器**：87 表建成但**触发器 = 0**。元数据可建 ≠ 迁移可重放。
5. **PGroonga 索引不在 PG relation 里**：`pg_class` 看不到、`pg_dump` 不导出索引数据、
   恢复时自动重建；容量规划会低估（2 万条时磁盘 8.5MB，SQL 侧读 0）。
6. **`pg_trgm` 对 CJK 完全不可用**（相似度全部低于阈值）；PGroonga 10/10 精确。
7. **post-filter 在低选择性下静默返回不足 k**（允许 1/10 空间时只剩 1 条）。

## 本次修掉的真实缺陷（都被测试或探针抓出）

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | 扫描器 `parents[4]` 解析错到 `.trellis` | 产出**空 inventory 却不报错** |
| 2 | 扫描器扫进 `backend/.venv` | 计数从 5 虚增到 23 |
| 3 | 扫描器默认输出到**已归档**任务目录 | 每次运行重建归档目录 |
| 4 | 按源码行数数触发器 | 数成 14，**漏掉 55 个**循环展开的 |
| 5 | sri 转换只插一列 | `NOT NULL` 违规 |
| 6 | 反证破坏性地 DROP 真实触发器 | 基线从 66 变 65 |
| 7 | 新增迁移缺 `run_ancestor_preflight` | 深层降级先 DROP 再被祖先拒绝（半降级） |
| 8 | `fence_execution` 在 `_settle` 顶部 | 「之后归还」= 反向锁序 |
| 9 | `capacity.release()` 内部 flush | 落库调用方脏状态，覆盖真实终态 |
| 10 | 门列进 ORM 映射 | 中间 revision 的迁移用例失败 |
| 11 | 锁序图先收自身锁再下钻 | 把**正确**顺序误报为反向 |
| 12 | 锁序图按函数名解析被调方 | 凭空造边，报出 **8 个假违反** |
| 13 | 锁序图未解析模块别名 | 漏掉真实的 counter 锁 |
| 14 | `@dataclass` 被新类挤走 | `EgressFailure()` 无参数 → 17 用例失败 |
| 15 | 误判 `eligibility` 带前导 AND | 拼出 `AND AND` 语法错误 |
| 16 | SQLite DATETIME 直接 `.isoformat()` | `/ready` 在 SQLite 上 500 |

## 验证

```
backend: 2060 passed, 33 skipped
mypy: 218 source files 无问题
ruff: 仅剩 main 上既有的 2 个（test_invitation_reachability）
迁移: upgrade head → downgrade base 往返通过；69 触发器保留
扫描器: 9/10 exit 0；build_pr_checks 无 DSN 时 exit 2（环境阻塞，不算通过）
变异: 每一处关键保护都有对应变异验证（见各 Gate 证据文件）
```

## 未完成项

全部登记在 `execution-ledger.md` 的 **ORCH-10 无主 TBD 清单**：每项有 owner、
依赖、恢复条件与下一命令。**不存在无主 TBD**。

C9/C10 的阻塞原因属本任务 PRD 的**停止条件**（接触真实环境 / 不可逆数据），
必须由用户决定时机。

## 集成顺序（硬约束）

本分支经 merge 携带了 `feat/10-03-postgres-migration` 的业务代码，**不得**直接
merge 进 main。正确顺序：

```bash
git checkout main
git merge feat/10-03-postgres-migration     # 依赖先合
git merge feat/10-06-architecture-completion-orchestration
```

## 环境清理

隔离容器已全部删除（`docker ps -a | grep fg-` 为空），SSH 隧道已关闭。
**线上与开发环境均未操作。**
