"""容量计数行的 bootstrap 命令行（C9/P0）。

用法：

```bash
# 只报告，不写入
python -m app.capacity_bootstrap --dry-run

# 建立/对齐计数行
python -m app.capacity_bootstrap

# 只对账（不写入），失败时非零退出——供 readiness 与 CI 使用
python -m app.capacity_bootstrap --reconcile
```

## 为什么是独立命令而不是自动执行

bootstrap 会写入计数行的 `active`。它只在**确认没有并发写入时**才是安全的
（赋值而非增量）。因此它是运维步骤，不是启动时的自动行为——启动时自动跑会在
多实例滚动重启时让两个实例同时赋值，互相覆盖。

启动时的**检查**由 `assert_ready` 承担：`pg_all` 阶段计数行不就绪即拒绝启动。
"""

from __future__ import annotations

import argparse
import sys

from app.db import SessionLocal
from app.services import capacity_bootstrap


def main() -> int:
    parser = argparse.ArgumentParser(description="容量计数行 bootstrap 与对账")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写入")
    parser.add_argument("--reconcile", action="store_true", help="只对账，不写入")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.reconcile:
            report = capacity_bootstrap.reconcile(db)
            print(f"对账：不一致 {len(report.mismatches)}，孤儿 {len(report.orphaned)}")
            for item in report.mismatches:
                print(f"  [不一致] {item}")
            for item in report.orphaned:
                print(f"  [孤儿]   {item}")
            return 0 if report.ok else 1

        report = capacity_bootstrap.bootstrap(db, dry_run=args.dry_run)
        db.commit()
        verb = "将建立" if args.dry_run else "已建立"
        print(
            f"{verb} {report.created} 条，已存在 {report.already_present} 条，"
            f"对齐 {len(report.adjusted)} 条"
        )
        for item in report.mismatches:
            print(f"  [不一致] {item}")
        if report.mismatches:
            print("存在真实占用超过容量的维度：不自动修正，请人工判断。")
            return 1
        if not args.dry_run:
            after = capacity_bootstrap.reconcile(db)
            if not after.ok:
                print("bootstrap 后对账失败：")
                for item in [*after.mismatches, *after.orphaned]:
                    print(f"  {item}")
                return 1
            print("bootstrap 后对账通过。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
