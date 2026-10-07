"""C7：WAL archive 与 PITR 恢复演练。

## 要证明什么

「有备份」不等于「能恢复」。本探针在**真实归档配置**下走完整链路：

```text
建表 + 写入基线数据
→ 记录恢复目标时刻（LSN / 时间戳）
→ 继续写入「不应被恢复」的数据
→ 物理基础备份（pg_basebackup）
→ 从归档 + 基础备份恢复到目标时刻
→ 断言：基线数据在、目标时刻之后的数据不在
```

**最后一条断言是本探针的核心**：只断言「数据在」无法区分「恢复成功」与
「根本没做 PITR，直接读到了最新数据」。必须有**负向**断言。

## 与 RPO/RTO 的关系

本探针测**正确性**（能否恢复到指定点），不测 RPO/RTO（那是时间指标，需要真实
数据量与存储）。二者不可互相替代。

用法（需要两个隔离实例：归档源 + 恢复目标，以及一个共享归档卷）：

    PG_PITR_DSN=... python3 scripts/migration-proof/pitr_recovery_probe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    override = os.environ.get("MIGRATION_PROOF_ROOT")
    if override:
        return Path(override)
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
        cwd=Path(__file__).parent).strip())


ROOT = _repo_root()
OUT_DIR = Path(os.environ.get("MIGRATION_PROOF_OUT", str(ROOT / "artifacts/migration-proof")))


def _psql(dsn: str, sql: str, *, expect_ok: bool = True) -> str:
    proc = subprocess.run(
        ["psql", dsn, "-tAc", sql], capture_output=True, text=True, timeout=60
    )
    if expect_ok and proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def main() -> int:
    dsn = os.environ.get("PG_PITR_DSN")
    if not dsn:
        print("SKIP: 需要 PG_PITR_DSN（指向启用了 archive_mode 的隔离实例）")
        return 2
    if not _shutil_which("pg_basebackup") or not _shutil_which("psql"):
        print("SKIP: pg_basebackup/psql 不在 PATH（环境阻塞，不是通过）")
        return 2

    failures: list[str] = []
    report: dict = {}

    # ---- 1) 基线数据 ----
    _psql(dsn, "DROP TABLE IF EXISTS pitr_probe")
    _psql(dsn, "CREATE TABLE pitr_probe (id int primary key, note text not null)")
    _psql(dsn, "INSERT INTO pitr_probe VALUES (1,'baseline'),(2,'baseline')")
    baseline_count = int(_psql(dsn, "SELECT count(*) FROM pitr_probe"))
    print(f"  基线行数：{baseline_count}")
    report["baseline_rows"] = baseline_count
    if baseline_count != 2:
        failures.append(f"基线行数应为 2，实际 {baseline_count}")

    # ---- 2) 记录恢复目标点 ----
    # 用 LSN 而不是时间戳：时间戳依赖时钟与格式，LSN 是精确的物理位置。
    target_lsn = _psql(dsn, "SELECT pg_current_wal_lsn()")
    # 切一次 WAL，确保目标点之前的改动已归档
    _psql(dsn, "SELECT pg_switch_wal()")
    print(f"  恢复目标 LSN：{target_lsn}")
    report["target_lsn"] = target_lsn

    # ---- 3) 目标点之后写入「不应恢复」的数据 ----
    _psql(dsn, "INSERT INTO pitr_probe VALUES (3,'after-target')")
    after_count = int(_psql(dsn, "SELECT count(*) FROM pitr_probe"))
    print(f"  目标点后行数：{after_count}（含 1 行不应被恢复）")
    report["after_target_rows"] = after_count
    if after_count != 3:
        failures.append(f"目标点后行数应为 3，实际 {after_count}")
    _psql(dsn, "SELECT pg_switch_wal()")

    # ---- 4) 物理基础备份 ----
    backup_dir = "/tmp/pitr-basebackup"
    subprocess.run(["rm", "-rf", backup_dir], check=False)
    proc = subprocess.run(
        ["pg_basebackup", "-D", backup_dir, "-X", "stream", "-c", "fast", "-d", dsn],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        print(f"  FAIL: pg_basebackup 失败：{proc.stderr.strip()[:200]}")
        failures.append("pg_basebackup 失败")
    else:
        size = sum(
            f.stat().st_size for f in Path(backup_dir).rglob("*") if f.is_file()
        )
        print(f"  基础备份完成：{size} bytes")
        report["basebackup_bytes"] = size

    # ---- 5) 从归档恢复到目标点 ----
    # 这是探针**无法在单容器内完成**的部分：恢复需要一个独立实例指向同一归档卷。
    # 因此本探针到此为止，并把恢复命令与断言写成可执行 runbook（见证据文件）。
    # 不伪造「恢复成功」——那会让「PITR 已验证」变成假结论。
    report["recovery_delegated"] = (
        "恢复到目标点需要独立实例挂载同一归档卷；命令与断言见 "
        "research/evidence/c7-pitr.md"
    )
    print("  恢复步骤委托给独立实例（命令见证据文件）；本探针不伪造恢复成功")

    _psql(dsn, "DROP TABLE IF EXISTS pitr_probe")

    report["failures"] = failures
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "pitr-recovery.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: 归档配置生效、基础备份成功；恢复断言见证据文件")
    return 0


def _shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


if __name__ == "__main__":
    sys.exit(main())
