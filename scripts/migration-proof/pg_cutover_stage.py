"""把 writer 阶段推进到目标值（C9 切流/回滚的**唯一**合法入口）。

## 为什么需要它，而不是直接改 env 或 UPDATE

`writer_state` 是切换真相，`FG_WRITER_STAGE` 只是**首次种子**。绕过 `advance()`
直接写库或改 env 会造成两个真实故障：

1. **epoch 不递增** → epoch 守卫永不触发（它靠比较 epoch 工作），旧实例不会失效，
   回滚也不生效。实测生产上就出现过 `stage=pg_all` 而 `epoch=0`。
2. **跳级** → 「哪些面已经切过」不可知，无法安全回滚。

因此本脚本只做一件事：**按相邻顺序调用 `writer_epoch.advance()`**，每一步都让
epoch +1 并落 `updated_by`。

## 用法

```bash
# 前进到目标阶段（自动逐级推进）
DATABASE_URL=... python scripts/migration-proof/pg_cutover_stage.py --to pg_all --actor ops

# 回滚一级
DATABASE_URL=... python scripts/migration-proof/pg_cutover_stage.py --to shadow --actor ops

# 只看当前状态
DATABASE_URL=... python scripts/migration-proof/pg_cutover_stage.py --show
```

**回滚只回退路由**：本脚本不搬运任何数据，也不把 PostgreSQL 的新状态写回 SQLite。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", help="目标阶段（sqlite/shadow/pg_control/pg_all）")
    parser.add_argument("--actor", default="ops", help="执行者标识（写入审计列）")
    parser.add_argument("--show", action="store_true", help="只显示当前状态")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        print("SKIP: 需要 DATABASE_URL（本脚本只用于 PostgreSQL 切流）")
        return 2

    from app.db import SessionLocal
    from app.services import writer_epoch

    with SessionLocal() as db:
        # 先播种（幂等）：env 的意图落库为初始行。之后一切变更只走 advance()。
        writer_epoch.seed_if_empty(db, actor="cutover-seed")
        db.commit()
        state = writer_epoch.read_state(db)
        print(f"当前: stage={state.stage} epoch={state.epoch} by={state.updated_by}")

        if args.show or not args.to:
            return 0

        if args.to not in writer_epoch.WRITER_STAGES:
            print(f"FAIL: 未知阶段 {args.to!r}；合法值 {writer_epoch.WRITER_STAGES}")
            return 1

        # 逐级推进（advance 只接受相邻阶段，这里显式循环而不是跳级）
        guard = 0
        while writer_epoch.read_state(db).stage != args.to:
            guard += 1
            if guard > len(writer_epoch.WRITER_STAGES) + 1:
                print("FAIL: 无法收敛到目标阶段（超过步数上限）")
                return 1
            current = writer_epoch.read_state(db).stage
            ci = writer_epoch.WRITER_STAGES.index(current)
            ti = writer_epoch.WRITER_STAGES.index(args.to)
            nxt = writer_epoch.WRITER_STAGES[ci + (1 if ti > ci else -1)]
            state = writer_epoch.advance(db, to_stage=nxt, actor=args.actor)
            db.commit()
            print(f"  推进: {current} -> {state.stage} (epoch={state.epoch})")

        final = writer_epoch.read_state(db)
        print(f"完成: stage={final.stage} epoch={final.epoch} by={final.updated_by}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
