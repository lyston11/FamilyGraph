# C7：WAL archive 与 PITR 恢复演练（完整执行）

## 实测结果

```
源实例（归档 + 持续写入）     : 3 行（baseline×2 + after-target×1）
恢复实例（PITR 到目标 LSN）   : 2 行（仅 baseline）
恢复实例中 id=3 的行数        : 0        ← 决定性负向断言
恢复日志: recovery stopping after WAL location (LSN) "0/F000028"
          database system is ready to accept connections
```

**恢复成功且精确**：目标点之后写入的 `id=3` **确实不在**恢复实例中。

## 为什么负向断言是核心

只断言「基线数据在」**无法区分**：

| 情形 | 基线数据在？ |
|---|---|
| PITR 正确恢复到目标点 | 是 |
| 根本没做 PITR，直接读到最新数据 | 是 |
| 从当前实例复制而非从归档恢复 | 是 |

三种情况都通过「数据在」的断言。因此必须断言**目标点之后的数据不在**——
这才是 PITR 的定义性质。

## 执行顺序（我第一次搞错了）

PITR 的顺序是**硬约束**：

```
建基线数据
→ 物理基础备份（此时只有基线）      ← 必须在目标点之前
→ 记录目标 LSN
→ 目标点之后写入「不应恢复」的数据
→ 从基础备份 + 归档恢复到目标 LSN
→ 断言：基线在、目标点后的数据不在
```

我第一次把基础备份放在**目标点之后**，得到：

```
FATAL: requested recovery stop point is before consistent recovery point
```

因为基础备份自身带一个 redo LSN，若目标点早于它，恢复无法开始。**基础备份必须在目标点之前**。

## 归档配置的两个坑（都踩到了）

1. **归档目录权限**：`/archive` 由 root 创建，postgres(uid 70) 无法写入 →
   `archive command failed with exit code 1`，`pg_stat_archiver` 显示
   `archived_count=0, failed_count=18`。必须 `chown 70:70`。
2. **`--volumes-from` 只共享卷，不共享文件系统**：基础备份写在源容器的
   共享卷内恢复容器才能看到；写在容器文件系统 `/tmp` 则看不到。

## 部署 runbook（可复跑）

```bash
# 1) 归档配置（postgresql.conf）
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /archive/%f && cp %p /archive/%f'

# 2) 归档目录必须可写（uid 70 = postgres）
chown -R 70:70 /archive

# 3) 基础备份（必须在目标点之前）
pg_basebackup -D /archive/bb -X stream -c fast -U postgres

# 4) 恢复：复制基础备份 → 写恢复配置 → 启动
cat >> $PGDATA/postgresql.auto.conf <<'EOF'
restore_command = 'cp /archive/%f %p'
recovery_target_lsn = '<目标 LSN>'
recovery_target_action = 'promote'
EOF
touch $PGDATA/recovery.signal

# 5) 验证（双向）
psql -c "SELECT count(*) FROM <表>"                          # 基线在
psql -c "SELECT count(*) FROM <表> WHERE id = <目标点后>"     # 必须为 0
```

## 证据等级

**L3**：真实 PostgreSQL 16、真实 `archive_mode=on`、真实 `pg_basebackup`、
真实从归档恢复到指定 LSN、真实 promote，含**决定性负向断言**。

## 仍未覆盖

- **RPO/RTO 时间指标**：需真实数据量与存储（本探针测正确性，不测时间）；
- **备份加密与异地存储**；
- **多时间线**（failover 后的 timeline 切换）；
- **大库备份时长与增量备份**；
- **HA / 自动 failover**：需多节点编排。
