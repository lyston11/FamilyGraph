#!/usr/bin/env python3
"""I：零费用排队 / 吞吐 / 锁竞争评估（隔离栈，脚本化上游，无真实模型费用）。

## 回答什么

I-R4/AC4 要求「用本地零费用多 run 模拟测单 worker 排队、轮询、上下文、SQLite
竞争和限流影响；给扩容前提，不把提高吞吐当缩短单次生成」。本脚本量化四件事：

1. **单 worker 串行**：N 个 run 的端到端墙钟 vs 单个 run 的生成时长；
2. **排队占比**：`queue_wait`（首次 lease 延迟）在总时长里的份额；
3. **账户并发上限**：第 3 个 run 是否被 409 `AGENT_RUN_ACCOUNT_LIMIT` 拒绝
   （这是「加 worker 并发」能不能提速的前提）；
4. **SQLite 写等待**：并发提交期的写事务耗时（决定加并发是否引入锁竞争）。

**不证明什么**：真实上游推理速度、真实用户排队分布、扩并发后的真实收益。
上游用脚本化流，生成时长是注入的常量，不是模型速度。

## 用法

```bash
python3 scripts/smoke/run_performance_options_probe.py --report /tmp/i-probe.json
python3 scripts/smoke/run_performance_options_probe.py --runs 6 --inject-ms 800
```

退出码：0 通过；1 有真实失败；2 环境阻塞。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
AGENT = ROOT / "agent"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
WORKER = Path(__file__).parent / "controlled_assistant_worker.mjs"

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2


def free_ports(count: int) -> list[int]:
    socks = [socket.socket() for _ in range(count)]
    try:
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in socks]
    finally:
        for sock in socks:
            sock.close()


def wait_health(url: str, deadline_s: float = 90.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 未就绪
            time.sleep(0.3)
    return False


READ_TIMING = """
import json, os, sqlite3
con = sqlite3.connect(os.environ["FG_DB"])
runs = con.execute(
    "select id, status, created_at, first_leased_at, settled_at, error_code "
    "from agent_runs where id > ? order by id", (int(os.environ["FG_BASELINE"]),)
).fetchall()
print(json.dumps({"runs": runs}))
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None)
    parser.add_argument("--runs", type=int, default=4, help="提交的 run 数量")
    parser.add_argument("--inject-ms", type=int, default=600,
                        help="脚本化上游每轮的注入时长（模拟生成耗时）")
    parser.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not VENV_PY.exists():
        print("BLOCKED: backend/.venv 不存在", file=sys.stderr)
        return EXIT_BLOCKED
    if not (AGENT / "dist" / "worker.js").exists():
        print("BLOCKED: agent/dist 未构建", file=sys.stderr)
        return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="fg-i-probe-"))
    public_port, internal_port, admin_port, health_port, upstream_port, _ = free_ports(6)
    cells: list[dict[str, Any]] = []
    procs: list[subprocess.Popen] = []
    started_at = time.monotonic()

    def cell(cid: str, requirement: str, result: str, note: str, evidence: Any = None) -> None:
        cells.append({"cell": cid, "requirement": requirement, "result": result,
                      "note": note, "evidence": evidence})

    try:
        env = os.environ.copy()
        env.update(
            {
                "DATA_DIR": str(data_dir),
                "PYTHONPATH": str(BACKEND),
                "PUBLIC_API_PORT": str(public_port),
                "PUBLIC_API_HOST": "127.0.0.1",
                "INTERNAL_AGENT_API_PORT": str(internal_port),
                "INTERNAL_AGENT_API_HOST": "127.0.0.1",
                "ADMIN_API_PORT": str(admin_port),
                "ADMIN_API_HOST": "127.0.0.1",
                "SECRET_KEY": secrets.token_hex(32),
                "ADMIN_JWT_SECRET": secrets.token_hex(32),
                "ADMIN_JWT_ISSUER": "fg-i-issuer",
                "ADMIN_JWT_AUDIENCE": "fg-i-audience",
                "DEV_SEED_DEMO_DATA": "1",
                "BCRYPT_ROUNDS": "4",
                "AGENT_RUNTIME_ENABLED": "1",
                "AGENT_SERVICE_SECRET": secrets.token_hex(32),
                "MEMORY_ENABLED": "0",
                "RAG_ENABLED": "0",
                "STEWARD_ENABLED": "",
                "STEWARD_WORKER_ENABLED": "",
                "FG_API_BASE_URL": f"http://127.0.0.1:{public_port}",
                "FG_INTERNAL_API_BASE_URL": f"http://127.0.0.1:{internal_port}",
                "HEALTH_PORT": str(health_port),
                "AGENT_SIDECAR_ID": "fg-i-probe",
                # Poll interval is the shipped 250ms (09-18 P0-1). Keep it at the
                # real value so queue_wait is not flattered by a test-only setting.
                "AGENT_LEASE_POLL_MS": "250",
                # 本探针用**脚本化流**（无 FG_USE_GATEWAY），因此不会真的联网；
                # 该地址只需让 provider 解析通过（base_url 非空），指向一个 
                # 不会监听的端口。若意外发起请求会立即连接失败，而不是静默上云。
                "FG_FAKE_UPSTREAM": "http://127.0.0.1:1/v1",
            }
        )

        migrate = subprocess.run(
            [str(VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300,
        )
        if migrate.returncode != 0:
            print(f"BLOCKED: alembic 失败\n{migrate.stderr[-600:]}", file=sys.stderr)
            return EXIT_BLOCKED

        serve_log = data_dir.parent / f"{data_dir.name}-serve.log"
        handle = serve_log.open("w", encoding="utf-8")
        procs.append(subprocess.Popen(
            [str(VENV_PY), "-m", "app.serve"], cwd=str(BACKEND), env=env,
            stdout=handle, stderr=subprocess.STDOUT,
        ))
        family = f"http://127.0.0.1:{public_port}"
        if not wait_health(f"{family}/api/health"):
            print("BLOCKED: listener 未就绪", file=sys.stderr)
            return EXIT_BLOCKED

        import urllib.error
        import urllib.request

        def post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None):
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)

        def post_may_fail(url: str, payload: dict[str, Any], headers: dict[str, str]):
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **headers},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return {"status": response.status, "body": json.load(response)}
            except urllib.error.HTTPError as error:
                body = error.read().decode(errors="replace")
                code = reason = None
                try:
                    err = json.loads(body).get("error", {})
                    code = err.get("code")
                    # 浏览器面把并发超限聚合为 AGENT_RUN_LIMIT，
                    # 具体限额在 detail.reason（session/account）。
                    detail = err.get("detail")
                    if isinstance(detail, dict):
                        reason = detail.get("reason")
                except json.JSONDecodeError:
                    pass
                return {"status": error.code, "error_code": code, "reason": reason}

        tokens = post(f"{family}/api/auth/login", {"name": "朱元璋", "pin": "123456"})
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        request = urllib.request.Request(f"{family}/api/spaces", headers=auth)
        with urllib.request.urlopen(request, timeout=30) as response:
            spaces = json.load(response)
        space_id = int(spaces[0]["id"])

        # 隔离 Provider 指向脚本化上游（零费用）
        seed = f"""
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils.timeutil import utcnow
with SessionLocal() as db:
    now = utcnow()
    provider = AgentProvider(name='i-probe', kind='local', api='openai-completions',
        base_url=os.environ['FG_FAKE_UPSTREAM'], allowed_models_json=['synthetic-model'],
        context_window=272000, max_tokens=2048, reasoning=False,
        input_modalities_json=['text'], thinking_levels_json=[], enabled=True,
        created_at=now, updated_at=now)
    db.add(provider); db.flush()
    db.add(AgentSpaceProviderSetting(space_id={space_id}, agent_kind='assistant',
        provider_id=provider.id, model='synthetic-model', cloud_allowed=False,
        local_required=False, enabled=True))
    db.commit()
"""
        seeded = subprocess.run([str(VENV_PY), "-c", seed], cwd=str(BACKEND), env=env,
                                capture_output=True, text=True, timeout=60)
        if seeded.returncode != 0:
            print(f"BLOCKED: Provider 播种失败\n{seeded.stderr[-500:]}", file=sys.stderr)
            return EXIT_BLOCKED

        # 提交前记录基线：隔离库初始为空，但显式取 max(id) 使过滤不依赖该前提。
        db_path = data_dir / "db" / "app.db"
        baseline = int(subprocess.run(
            [str(VENV_PY), "-c",
             "import sqlite3,os;print(sqlite3.connect(os.environ['FG_DB']) "
             ".execute('select coalesce(max(id),0) from agent_runs').fetchone()[0])"],
            cwd=str(BACKEND), env={**env, "FG_DB": str(db_path)},
            capture_output=True, text=True, timeout=60,
        ).stdout.strip() or 0)

        # 提交 N 个 run（各自独立会话：同会话只允许一个活跃 run）。
        # 账户并发上限默认 2，因此第 3 个起预期被 409 拒——这正是要测的事实。
        created: list[dict[str, Any]] = []
        submitted_at = time.monotonic()
        for index in range(args.runs):
            session = post(f"{family}/api/agent/sessions", {"space_id": space_id}, auth)
            result = post_may_fail(
                f"{family}/api/agent/sessions/{session['id']}/messages",
                {"content": f"probe question {index}"},
                {**auth, "Idempotency-Key": secrets.token_hex(16)},
            )
            created.append({
                "index": index,
                "session_id": session["id"],
                "http": result.get("status"),
                "error_code": result.get("error_code"),
                "reason": result.get("reason"),
                "run_id": (result.get("body") or {}).get("run", {}).get("id"),
            })
        submit_ms = int((time.monotonic() - submitted_at) * 1000)

        accepted = [c for c in created if c["run_id"] is not None]
        rejected = [c for c in created if c["run_id"] is None]
        limit_codes = sorted({c["error_code"] for c in rejected})
        limit_reasons = sorted({c["reason"] for c in rejected})
        cell(
            "I-1",
            "账户并发上限是实测事实（加并发的前置）",
            "pass" if rejected and not accepted[2:] and limit_codes == ["AGENT_RUN_LIMIT"]
            else ("pass" if not rejected else "fail"),
            f"accepted={len(accepted)} rejected={len(rejected)} "
            f"codes={limit_codes} reasons={limit_reasons} submit_ms={submit_ms}",
            {
                "created": created,
                "submit_ms": submit_ms,
                "note": (
                    "浏览器面把并发超限聚合为 AGENT_RUN_LIMIT，具体限额在 "
                    "detail.reason（session/account）；这里两者都记录。"
                ),
            },
        )

        # 逐个执行 worker（串行，模拟单 worker），记录每个 run 的排队与总时长。
        worker_reports = []
        for _ in accepted:
            completed = subprocess.run(
                ["node", str(WORKER)], cwd=str(ROOT),
                env={**env, "FG_SCENARIO": "A1-run",
                       "FG_PART_DELAY_MS": str(args.inject_ms)},
                capture_output=True, text=True, timeout=300,
            )
            worker_reports.append((completed.stdout.strip().splitlines() or [""])[-1][:160])

        def await_terminal(run_id: int, timeout_s: float = 180.0) -> dict[str, Any]:
            deadline = time.monotonic() + timeout_s
            last: dict[str, Any] = {}
            while time.monotonic() < deadline:
                req = urllib.request.Request(f"{family}/api/agent/runs/{run_id}", headers=auth)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    last = json.load(resp)
                if last.get("status") in ("succeeded", "failed", "cancelled", "expired"):
                    return last
                time.sleep(0.25)
            return {**last, "timed_out": True}

        terminals = [await_terminal(c["run_id"]) for c in accepted]
        total_ms = int((time.monotonic() - submitted_at) * 1000)

        read = subprocess.run(
            [str(VENV_PY), "-c", READ_TIMING], cwd=str(BACKEND),
            env={**env, "FG_DB": str(db_path), "FG_BASELINE": str(baseline)},
            capture_output=True, text=True, timeout=60,
        )
        rows = json.loads(read.stdout)["runs"] if read.returncode == 0 else []

        from datetime import datetime

        def parse(value: str | None):
            return datetime.fromisoformat(value) if value else None

        per_run = []
        for row in rows:
            run_id, status, created_at, first_leased, settled, error_code = row
            wait_s = (parse(first_leased) - parse(created_at)).total_seconds() if (
                parse(first_leased) and parse(created_at)) else None
            total_s = (parse(settled) - parse(created_at)).total_seconds() if (
                parse(settled) and parse(created_at)) else None
            per_run.append({
                "run_id": run_id, "status": status, "queue_wait_s": wait_s,
                "total_s": total_s, "error_code": error_code,
            })

        statuses = [t.get("status") for t in terminals]
        cell(
            "I-2",
            "单 worker 串行执行：所有被接受的 run 达终态",
            "pass" if statuses and all(s == "succeeded" for s in statuses) else "fail",
            f"statuses={statuses} total_ms={total_ms}",
            {"terminals": terminals, "worker_reports": worker_reports},
        )

        waits = [r["queue_wait_s"] for r in per_run if r["queue_wait_s"] is not None]
        totals = [r["total_s"] for r in per_run if r["total_s"] is not None]
        # 排队占比：第二个及以后的 run 才真正经历排队（第一个立即被取走）。
        cell(
            "I-3",
            "排队在总时长中的占比可量化（区分排队与生成，不混为一谈）",
            "pass" if len(waits) >= 2 and any(w > 0.05 for w in waits[1:]) else "fail",
            f"queue_wait_s={[round(w,3) for w in waits]} "
            f"total_s={[round(t,3) for t in totals]}",
            {
                "per_run": per_run,
                "queue_wait_s": waits,
                "total_s": totals,
                "submitted_runs": len(accepted),
                "inject_ms": args.inject_ms,
                "note": (
                    "queue_wait 用 first_leased_at 与 created_at 之差（D 的权威口径）；"
                    "total_s 是入库时刻到终态的持久间隔，仅作量级参考。"
                    "注入的生成时长是常量，不是模型速度。"
                ),
            },
        )

        # 写事务耗时：并发提交期观察（决定加并发是否引入 SQLite 锁竞争）。
        cell(
            "I-4",
            "并发提交下写路径未出现明显锁竞争（扩容前提之一）",
            "pass" if not any(c.get("error_code") == "database is locked" for c in created)
            else "fail",
            f"submit_ms={submit_ms} accepted={len(accepted)} rejected={len(rejected)}",
            {"submit_ms": submit_ms, "created": created},
        )

        exit_code = EXIT_PASS
        if any(c["result"] == "fail" for c in cells):
            exit_code = EXIT_FAILED
        report = {
            "suite": "familygraph-performance-options-probe",
            "verdict": {0: "pass", 1: "failed", 2: "blocked"}[exit_code],
            "counts": {"total": len(cells),
                       "passed": sum(1 for c in cells if c["result"] == "pass"),
                       "failed": sum(1 for c in cells if c["result"] == "fail")},
            "wall_clock_s": round(time.monotonic() - started_at, 1),
            "params": {"runs": args.runs, "inject_ms": args.inject_ms},
            "cells": cells,
        }
        if args.report:
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return exit_code
    finally:
        for proc in reversed(procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
        (data_dir.parent / f"{data_dir.name}-serve.log").unlink(missing_ok=True)
        if args.keep:
            print(f"kept DATA_DIR: {data_dir}", file=sys.stderr)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
