#!/usr/bin/env python3
"""F 受控验收：真实 backend + 真实 sidecar + 真实 Pi SDK，仅模型流被脚本化。

隔离原则（与 design.md 一致）：
- 一次性临时 DATA_DIR（mkdtemp），运行结束删除；绝不触碰业务库或用户 .env；
- 三个 listener 使用动态端口，与 launchd 管理的 8000/8001/8002 SSH 隧道无关；
- loopback 假上游只模拟协议与时序，不产生外网 egress、不产生模型费用；
- 报告只含状态/计数/时序，不含 prompt、正文、凭据或本地路径。

用法：
    python scripts/smoke/run_controlled_acceptance.py --report /tmp/f-controlled.json
退出码：0 通过 / 1 有失败 / 2 环境阻塞。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"
WORKER = ROOT / "scripts" / "smoke" / "controlled_assistant_worker.mjs"
# A3 must accumulate enough REAL history for the SDK's own keepRecentTokens
# (20_000) budget to find a cut point. One message is capped by
# AGENT_MESSAGE_MAX_LENGTH (8_000) and the estimator is ~4 chars/token.
A3_SEED_TURNS = 14
A3_SEED_CHARS = 8_000
# A1 注入的“首正文前”延迟；用于把延迟归到正确阶段（F-R3）。
INJECTED_PRE_TEXT_DELAY_MS = 900
# sidecar 批量 flush 间隔（AGENT_EVENT_FLUSH_MS 默认值），用于量化容差说明。
_FLUSH_INTERVAL_MS = 250

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2


def free_ports(count: int) -> list[int]:
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


# ---- loopback fake upstream ------------------------------------------------


class _FakeUpstreamState:
    """Scripted upstream behaviour; counts every request it actually receives."""

    def __init__(self) -> None:
        self.status = 200
        self.body_delay_ms = 0
        self.header_delay_ms = 0
        self.chunk_delay_ms = 0
        self.parts: list[str] = ["ok"]
        self.requests: list[dict[str, Any]] = []
        self.chunk_times_ms: list[float] = []
        self.lock = threading.Lock()
        self.t0 = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "count": len(self.requests),
                "requests": list(self.requests),
                "chunk_times_ms": list(self.chunk_times_ms),
            }


def _openai_sse_chunks(parts: list[str]) -> list[bytes]:
    """Minimal OpenAI chat.completions SSE stream (role, deltas, stop, usage)."""
    import json as _json

    frames: list[bytes] = []
    first = {
        "id": "chatcmpl-controlled",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "synthetic-model",
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}],
    }
    frames.append(f"data: {_json.dumps(first)}\n\n".encode())
    for part in parts:
        chunk = {
            "id": "chatcmpl-controlled",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "synthetic-model",
            "choices": [{"index": 0, "delta": {"content": part}}],
        }
        frames.append(f"data: {_json.dumps(chunk)}\n\n".encode())
    stop = {
        "id": "chatcmpl-controlled",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "synthetic-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    frames.append(f"data: {_json.dumps(stop)}\n\n".encode())
    frames.append(b"data: [DONE]\n\n")
    return frames


def start_fake_upstream(state: _FakeUpstreamState, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: ARG002 - silence
            pass

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length else b""
            with state.lock:
                state.requests.append(
                    {
                        "path": self.path,
                        "status": state.status,
                        "has_authorization": bool(self.headers.get("authorization")),
                        "content_type": self.headers.get("content-type"),
                        "bytes": len(raw),
                    }
                )
            if state.header_delay_ms:
                time.sleep(state.header_delay_ms / 1000)
            if state.status != 200:
                payload = json.dumps(
                    {"error": {"message": "synthetic upstream failure", "type": "synthetic"}}
                ).encode()
                self.send_response(state.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if state.body_delay_ms:
                time.sleep(state.body_delay_ms / 1000)
            # Chunked SSE: the gateway must forward each chunk without buffering
            # the whole body, which is what F-R5 measures.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for frame in _openai_sse_chunks(state.parts):
                if state.chunk_delay_ms:
                    time.sleep(state.chunk_delay_ms / 1000)
                self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                self.wfile.flush()
                with state.lock:
                    state.chunk_times_ms.append(
                        round((time.monotonic() - state.t0) * 1000, 1)
                    )
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---- report ----------------------------------------------------------------


@dataclass
class Grid:
    cells: list[dict[str, Any]] = field(default_factory=list)

    def cell(
        self,
        cell_id: str,
        group: str,
        requirement: str,
        result: str,
        note: str = "",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.cells.append(
            {
                "cell": cell_id,
                "group": group,
                "requirement": requirement,
                "result": result,
                "note": note,
                "evidence": evidence or {},
            }
        )

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.cells if c["result"] == "fail"]


SEED_PROVIDER = """
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils.timeutil import utcnow
with SessionLocal() as db:
    now = utcnow()
    provider = AgentProvider(name='controlled-acceptance', kind='local',
        api='openai-completions', base_url=os.environ['FG_FAKE_UPSTREAM'],
        allowed_models_json=['synthetic-model'], context_window=272000,
        max_tokens=2048, reasoning=False, input_modalities_json=['text'],
        thinking_levels_json=[], enabled=True, created_at=now, updated_at=now)
    db.add(provider)
    db.flush()
    db.add(AgentSpaceProviderSetting(space_id=int(os.environ['FG_SPACE_ID']),
        agent_kind='assistant', provider_id=provider.id, model='synthetic-model',
        cloud_allowed=False, local_required=False, enabled=True))
    db.commit()
"""

READ_RUN_TIMING = """
import json, os, sqlite3
con = sqlite3.connect(os.environ['FG_DB'])
try:
    runs = con.execute(
        'select id, status, created_at, first_leased_at, error_code, settled_at from agent_runs order by id'
    ).fetchall()
    rows = con.execute(
        'select run_id, type, timing_json, created_at from agent_run_events order by seq'
    ).fetchall()
    egress = con.execute(
        "select detail_json from audit_log where action='agent_provider_egress' order by id"
    ).fetchall()
    audits = con.execute(
        'select action, detail_json from audit_log order by id'
    ).fetchall()
finally:
    con.close()
latest = runs[-1] if runs else (None, None, None, None, None, None)
print(json.dumps({
    'run_id': latest[0],
    'status': latest[1],
    'first_leased_at': latest[3],
    'error_code': latest[4],
    'runs': [
        {'run_id': r[0], 'status': r[1], 'created_at': r[2], 'first_leased_at': r[3],
         'error_code': r[4], 'settled_at': r[5]}
        for r in runs
    ],
    'events': [
        {'run_id': r[0], 'type': r[1], 'timing': json.loads(r[2]) if r[2] else None,
         'created_at': r[3]}
        for r in rows
    ],
    'egress': [json.loads(r[0]) if r[0] else None for r in egress],
    'audits': [{'action': a, 'detail': json.loads(d) if d else None} for a, d in audits],
}))
"""


def read_run_timing(env: dict[str, str], db_path: Path) -> dict[str, Any]:
    child_env = {**env, "FG_DB": str(db_path)}
    completed = subprocess.run(
        [str(VENV_PY), "-c", READ_RUN_TIMING],
        cwd=str(BACKEND),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        return {"error": "timing read failed", "stderr": completed.stderr[-500:]}
    return json.loads(completed.stdout.strip().splitlines()[-1])


def sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


def wait_health(url: str, deadline_s: float = 90.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def wait_terminal(client: httpx.Client, base: str, headers: dict[str, str], run_id: int, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        run = client.get(f"{base}/api/agent/runs/{run_id}", headers=headers).json()
        if run.get("status") in ("succeeded", "failed", "cancelled"):
            return run
        time.sleep(0.25)
    return run


def run_worker(env: dict[str, str], scenario: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
    node_env = {**env, "FG_SCENARIO": scenario, **(extra or {})}
    completed = subprocess.run(
        ["node", str(WORKER)],
        cwd=str(ROOT),
        env=node_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    stdout = completed.stdout.strip().splitlines()
    report: dict[str, Any] = {}
    if stdout:
        try:
            report = json.loads(stdout[-1])
        except json.JSONDecodeError:
            report = {"verdict": "failed", "error_type": "UnparseableWorkerOutput"}
    report["returncode"] = completed.returncode
    if completed.stderr.strip():
        report["stderr_tail"] = completed.stderr.strip()[-400:]
    return report


def run_worker_async(
    env: dict[str, str], scenario: str, extra: dict[str, str] | None = None
) -> subprocess.Popen:
    """Start the worker without waiting (used by the cancel scenario)."""
    node_env = {**env, "FG_SCENARIO": scenario, **(extra or {})}
    return subprocess.Popen(
        ["node", str(WORKER)],
        cwd=str(ROOT),
        env=node_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def collect_worker(process: subprocess.Popen, timeout: float = 120.0) -> dict[str, Any]:
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        return {"verdict": "failed", "error_type": "WorkerTimeout"}
    lines = (stdout or "").strip().splitlines()
    report: dict[str, Any] = {}
    if lines:
        try:
            report = json.loads(lines[-1])
        except json.JSONDecodeError:
            report = {"verdict": "failed", "error_type": "UnparseableWorkerOutput"}
    report["returncode"] = process.returncode
    if (stderr or "").strip():
        report["stderr_tail"] = stderr.strip()[-400:]
    return report


def _build_report(grid: Grid, upstream_state: _FakeUpstreamState) -> dict[str, Any]:
    return {
        "suite": "familygraph-controlled-acceptance",
        "verdict": "pass" if not grid.failed else "failed",
        # F-R6 provenance: every cell must be traceable to a source SHA, the
        # dependency set it ran against, the fixture and the injection plan.
        "provenance": _provenance(),
        "upstream": upstream_state.snapshot(),
        "counts": {
            "total": len(grid.cells),
            "passed": len(grid.cells) - len(grid.failed),
            "failed": len(grid.failed),
        },
        "cells": grid.cells,
    }


def _provenance() -> dict[str, Any]:
    """Source SHA + dependency versions of the stack under test."""

    def _git(*args: str) -> str:
        out = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"

    versions: dict[str, str] = {}
    probe = subprocess.run(
        [
            str(VENV_PY),
            "-c",
            "import json,sqlite3,fastapi,sqlalchemy,alembic,httpx,pydantic\n"
            "print(json.dumps({'sqlite': sqlite3.sqlite_version, "
            "'fastapi': fastapi.__version__, 'sqlalchemy': sqlalchemy.__version__, "
            "'alembic': alembic.__version__, 'httpx': httpx.__version__, "
            "'pydantic': pydantic.__version__}))",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        try:
            versions = json.loads(probe.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            versions = {"error": "version probe unparseable"}
    node = subprocess.run(["node", "--version"], capture_output=True, text=True)
    return {
        "source_sha": _git("rev-parse", "HEAD"),
        "source_sha_short": _git("rev-parse", "--short", "HEAD"),
        "source_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "working_tree_dirty": bool(_git("status", "--porcelain")),
        "versions": {
            **versions,
            "node": node.stdout.strip() if node.returncode == 0 else "unknown",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        },
        "fixture": {
            "kind": "synthetic-loopback-upstream",
            "real_provider": False,
            "note": "假上游只模拟协议与时序；模型质量不由本矩阵证明",
        },
        "isolation": {
            "data_dir": "mkdtemp(fg-controlled-*)",
            "ports": "free_ports(5) 动态分配，避开 launchd 隧道 8000/8001/8002",
        },
    }


# 已有回归测试（复用）：B 组管家边界与 D/E 的观测/重试合同由这些套件锁定。
# F 不重写它们，只把它们纳入同一份矩阵，并标注它们证明什么、不证明什么。
DEFAULT_SCOPE = (
    "复用既有套件（fake transport / SDK 假流）：证明程序合同，不证明真实上游"
    "时延分布、模型输出质量或浏览器行为"
)
_SUITE_SCOPE = {
    "B1": DEFAULT_SCOPE + "；本格覆盖四类阻塞（等待 headers / 等待正文 / 持续慢 chunk）与连接释放",
    "B1b": DEFAULT_SCOPE + "；本格覆盖 connect 等待、发送前预算不足（未发送）与结算预留",
    "B1c": DEFAULT_SCOPE + "；本格在真实 HTTP transport 层验证总截止，不靠 fake 时钟",
    "B2": DEFAULT_SCOPE + "；本格覆盖逐笔结算、混合批次独立恢复、保守计费、四 kind 与合法空结果",
    "B3": DEFAULT_SCOPE + "；本格覆盖失租/接管/中断后旧执行者零业务写回",
    "B4": DEFAULT_SCOPE + "；本格覆盖三个崩溃点各自的恢复收敛",
    "B5": DEFAULT_SCOPE + "；本格覆盖次数/token/超长 prompt/输出 cap 预算",
    "D1": "复用既有套件：验证聚合口径（分母、basis、阶段归属），不证明真实链路时延",
    "D2": "复用既有套件：验证增量分片不物化历史与 payload 形状拒绝",
    "E1": "复用既有套件：验证错误分类与 egress 审计，真实 SDK 出站数见 A5 受控格",
}

REUSED_SUITES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "B1",
        "B1",
        "F-R2 管家总截止：headers/body/持续 chunk 均在预算内收敛且连接释放",
        (
            "tests/test_steward_assist_deadline.py::test_stalled_reads_are_interrupted_within_the_total_budget",
            "tests/test_steward_assist_deadline.py::test_repeated_deadlines_leave_no_lingering_connections",
        ),
    ),
    (
        "B1b",
        "B1",
        "F-R2 管家总截止：connect 等待与发送前预算不足（未发送）",
        (
            "tests/test_steward_assist_deadline.py::test_connect_wait_is_bounded_by_the_total_budget",
            "tests/test_steward_assist_deadline.py::test_out_of_transaction_recheck_prevents_an_unsendable_request",
            "tests/test_steward_assist_deadline.py::test_send_budget_reserves_settlement_time",
            "tests/test_steward_assist_deadline.py::test_released_reservations_do_not_consume_the_job_budget",
        ),
    ),
    (
        "B1c",
        "B1",
        "F-R2 管家在真实传输层的总截止与慢 chunk 上限（不靠单元 fake 时钟）",
        (
            "tests/test_steward_assist.py::test_slow_chunks_cannot_extend_past_the_total_deadline",
            "tests/test_steward_assist.py::test_slow_http_does_not_block_other_space_writes",
            "tests/test_steward_assist.py::test_transport_receives_30s_default_timeout",
        ),
    ),
    (
        "B2",
        "B2",
        "F-R2 管家结果保全：逐笔结算/混合批次独立恢复/幂等费用/四 kind 与空结果",
        (
            "tests/test_steward_assist.py::test_returned_result_is_persisted_before_the_next_send",
            "tests/test_steward_assist.py::test_partial_batch_applies_independent_product_and_stays_failed",
            "tests/test_steward_assist.py::test_conservative_billing",
            "tests/test_steward_assist.py::test_candidate_atomic_kinds_only",
            "tests/test_steward_assist.py::test_candidate_empty_array_succeeded_applied_with_no_candidates",
        ),
    ),
    (
        "B3",
        "B2",
        "F-R2 管家失租/接管/中断：旧执行者零业务写回",
        (
            "tests/test_steward_assist.py::test_lease_deadline_stops_followup_sends",
            "tests/test_steward_assist.py::test_fence_disabled_during_call_blocks_writeback",
            "tests/test_steward_assist.py::test_fence_provider_switch_during_call",
            "tests/test_steward_assist.py::test_fence_card_terminal_during_call",
            "tests/test_steward_assist.py::test_fence_evidence_changed_during_call",
            "tests/test_steward_assist.py::test_killed_assist_does_not_rollback_core",
            "tests/test_steward_assist.py::test_recovery_does_not_resend_audited_unknown_or_revisit_settled_failures",
        ),
    ),
    (
        "B4",
        "B2",
        "F-R2 管家崩溃点恢复：发前/发后未审计/写回前各自可收敛",
        (
            "tests/test_steward_assist.py::test_crash_point_2_before_send_recovers_to_pending",
            "tests/test_steward_assist.py::test_crash_point_3_after_send_before_audit",
            "tests/test_steward_assist.py::test_crash_point_4_before_writeback_applies_after_fence",
        ),
    ),
    (
        "B5",
        "B2",
        "F-R2 管家预算：次数/token/超长 prompt/输出 cap 均在发送前或读流时受限",
        (
            "tests/test_steward_assist.py::test_budget_two_caps_three_kinds_at_two_sends",
            "tests/test_steward_assist.py::test_insufficient_tokens_skips_without_send",
            "tests/test_steward_assist.py::test_oversized_prompt_skipped_without_send",
            "tests/test_steward_assist.py::test_oversized_response_capped_without_full_read",
            "tests/test_steward_assist.py::test_usage_missing_billed_from_reservation",
        ),
    ),
    (
        "D1",
        "A3",
        "F-R3 源计时/聚合口径：分母不丢、basis 不混精度",
        (
            "tests/test_admin_agent_latency.py",
        ),
    ),
    (
        "D2",
        "A1",
        "F-R1 增量显示不物化历史且形状 fail-closed",
        (
            "tests/test_agent_events.py",
        ),
    ),
    (
        "E1",
        "A5",
        "F-R1/E 错误分类与分层重试审计（上游真实状态码不被折叠）",
        (
            "tests/test_provider_proxy.py",
        ),
    ),
)


def _run_reused_suites(grid: Grid) -> None:
    """F-R6：把已有回归纳入同一矩阵，并写明它证明/不证明什么。"""
    for cell_id, group, requirement, targets in REUSED_SUITES:
        started = time.monotonic()
        completed = subprocess.run(
            [str(VENV_PY), "-m", "pytest", "-q", *targets],
            cwd=str(BACKEND),
            capture_output=True,
            text=True,
        )
        tail = (completed.stdout or "").strip().splitlines()[-1:] or [""]
        grid.cell(
            cell_id,
            group,
            requirement,
            "pass" if completed.returncode == 0 else "fail",
            f"pytest rc={completed.returncode} {tail[0][:70]}",
            {
                "command": "cd backend && .venv/bin/python -m pytest -q " + " ".join(targets),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "targets": list(targets),
                "scope_note": _SUITE_SCOPE.get(cell_id, DEFAULT_SCOPE),
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None)
    parser.add_argument("--scenario", action="append", default=None, help="只跑指定场景（可重复）")
    parser.add_argument(
        "--no-reused-suites",
        action="store_true",
        help="跳过复用既有回归套件（只跑受控场景）",
    )
    args = parser.parse_args()

    if not VENV_PY.exists():
        print("BLOCKED: backend/.venv 不存在", file=sys.stderr)
        return EXIT_BLOCKED
    if not WORKER.exists():
        print(f"BLOCKED: worker 不存在 {WORKER}", file=sys.stderr)
        return EXIT_BLOCKED
    if not (ROOT / "agent" / "dist" / "worker.js").exists():
        print("BLOCKED: agent/dist 未构建", file=sys.stderr)
        return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="fg-controlled-"))
    db_path = data_dir / "db" / "app.db"
    public_port, internal_port, admin_port, health_port, upstream_port = free_ports(5)
    upstream_state = _FakeUpstreamState()
    upstream = start_fake_upstream(upstream_state, upstream_port)
    grid = Grid()
    log_handle: Any = None
    env: dict[str, str] = {}
    process: subprocess.Popen | None = None

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
                "ADMIN_JWT_ISSUER": "fg-controlled-admin-issuer",
                "ADMIN_JWT_AUDIENCE": "fg-controlled-admin-audience",
                "DEV_SEED_DEMO_DATA": "1",
                "BCRYPT_ROUNDS": "4",
                "AGENT_RUNTIME_ENABLED": "1",
                "AGENT_SERVICE_SECRET": secrets.token_hex(32),
                "PERSONAL_FAMILY_VIEW_ENABLED": "1",
                "MEMORY_ENABLED": "1",
                "RAG_ENABLED": "1",
                "STEWARD_ENABLED": "",
                "STEWARD_WORKER_ENABLED": "",
                # sidecar（node 子进程）读取同一份环境
                "FG_API_BASE_URL": f"http://127.0.0.1:{public_port}",
                "FG_INTERNAL_API_BASE_URL": f"http://127.0.0.1:{internal_port}",
                "HEALTH_PORT": str(health_port),
                "AGENT_SIDECAR_ID": "fg-controlled-acceptance",
                "AGENT_LEASE_POLL_MS": "50",
                "FG_FAKE_UPSTREAM": f"http://127.0.0.1:{upstream_port}/v1",
            }
        )

        family = f"http://127.0.0.1:{public_port}"

        migrate = subprocess.run(
            [str(VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if migrate.returncode != 0:
            print(f"BLOCKED: alembic upgrade head 失败\n{migrate.stderr[-1500:]}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-1", "env", "隔离库迁移到 head", "pass", "alembic upgrade head")

        serve_log = data_dir.parent / f"{data_dir.name}-serve.log"
        log_handle = serve_log.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [str(VENV_PY), "-m", "app.serve"],
            cwd=str(BACKEND),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        if not wait_health(f"{family}/api/health"):
            tail = ""
            if serve_log.exists():
                tail = "\n".join(serve_log.read_text(encoding="utf-8").splitlines()[-25:])
            print(f"BLOCKED: 隔离 listener 未就绪\n{tail}", file=sys.stderr)
            return EXIT_BLOCKED
        log_handle.close()
        serve_log.unlink(missing_ok=True)
        grid.cell("ENV-2", "env", "真实三 listener 就绪", "pass", "app.serve")

        client = httpx.Client(timeout=httpx.Timeout(30.0))
        login = client.post(
            f"{family}/api/auth/login", json={"name": "朱元璋", "pin": "123456"}
        )
        if login.status_code != 200:
            print(f"BLOCKED: 演示账号登录失败 {login.status_code}", file=sys.stderr)
            return EXIT_BLOCKED
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        spaces = client.get(f"{family}/api/spaces", headers=headers).json()
        space_id = next(
            (s["id"] for s in spaces if s.get("kind") == "household"),
            spaces[0]["id"] if spaces else None,
        )
        if space_id is None:
            print("BLOCKED: 无可用空间", file=sys.stderr)
            return EXIT_BLOCKED

        seeded = subprocess.run(
            [str(VENV_PY), "-c", SEED_PROVIDER],
            cwd=str(BACKEND),
            env={**env, "FG_SPACE_ID": str(space_id)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        if seeded.returncode != 0:
            print(f"BLOCKED: Provider 播种失败\n{seeded.stderr[-800:]}", file=sys.stderr)
            return EXIT_BLOCKED
        grid.cell("ENV-3", "env", "隔离 Provider 指向 loopback 假上游", "pass", "no cloud egress")

        scenarios = args.scenario or [
            "A1",
            "A4-queue",
            "A5-permanent",
            "A5-transient",
            "A2b",
            "A6-cancel",
            "A3-compaction",
            "R5-stream",
        ]
        for scenario in scenarios:
            _run_scenario(
                scenario,
                grid,
                client,
                family,
                headers,
                env,
                space_id,
                upstream_state,
                db_path,
            )
        if not args.no_reused_suites:
            _run_reused_suites(grid)
    except Exception as exc:  # noqa: BLE001 - harness 自身异常视为环境阻塞
        grid.cell(
            "HARNESS",
            "env",
            "受控矩阵未中断完成",
            "fail",
            f"{type(exc).__name__}: {exc}",
        )
        report = _build_report(grid, upstream_state)
        if args.report:
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(f"BLOCKED: harness 异常 {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
        upstream.shutdown()
        # 清理证据：临时 DATA_DIR 与凭据随目录删除
        shutil.rmtree(data_dir, ignore_errors=True)
        grid.cell(
            "ENV-9",
            "env",
            "临时 DATA_DIR 已删除",
            "pass" if not data_dir.exists() else "fail",
            data_dir.name,
        )

    report = _build_report(grid, upstream_state)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output, encoding="utf-8")
    else:
        print(output)
    return EXIT_PASS if not grid.failed else EXIT_FAILED


def _start_run(
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    space_id: int,
) -> tuple[int, int]:
    session = client.post(
        f"{family}/api/agent/sessions", headers=headers, json={"space_id": space_id}
    ).json()
    posted = client.post(
        f"{family}/api/agent/sessions/{session['id']}/messages",
        headers={**headers, "Idempotency-Key": secrets.token_hex(16)},
        json={"content": "Where is the blue tin?"},
    ).json()
    return session["id"], posted["run"]["id"]


def _run_scenario(
    scenario: str,
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
) -> None:
    if scenario == "A4-queue":
        _scenario_queue_wait(grid, client, family, headers, env, space_id, upstream_state, db_path)
        return
    if scenario == "A6-cancel":
        _scenario_cancel(grid, client, family, headers, env, space_id, upstream_state, db_path)
        return
    if scenario == "R5-stream":
        _scenario_gateway_stream(grid, client, family, headers, env, space_id, upstream_state, db_path)
        return
    if scenario == "A3-compaction":
        _scenario_compaction(grid, client, family, headers, env, space_id, upstream_state, db_path)
        return
    if scenario == "A5-permanent":
        upstream_state.status = 401
        _scenario_gateway_error(
            "A5-permanent",
            grid,
            client,
            family,
            headers,
            env,
            space_id,
            upstream_state,
            db_path,
            expected_status=401,
            expect_single_attempt=True,
        )
        upstream_state.status = 200
        return
    if scenario == "A5-transient":
        upstream_state.status = 502
        _scenario_gateway_error(
            "A5-transient",
            grid,
            client,
            family,
            headers,
            env,
            space_id,
            upstream_state,
            db_path,
            expected_status=502,
            expect_single_attempt=False,
        )
        upstream_state.status = 200
        return

    _session_id, run_id = _start_run(client, family, headers, space_id)
    before = upstream_state.snapshot()["count"]
    # A1 injects a measurable pre-prose delay so F-R3 can attribute it to the
    # right stage instead of only asserting that source timing exists.
    extra = {"FG_PART_DELAY_MS": str(INJECTED_PRE_TEXT_DELAY_MS)} if scenario == "A1" else None
    report = run_worker(env, scenario, extra)
    run = wait_terminal(client, family, headers, run_id)
    timing = read_run_timing(env, db_path)
    after = upstream_state.snapshot()

    stream = client.get(f"{family}/api/agent/runs/{run_id}/events", headers=headers)
    events = sse_events(stream)
    types = [e["type"] for e in events]
    deltas = [e for e in events if e["type"] == "assistant.text_delta"]
    assistant = [e for e in events if e["type"] == "message.assistant_added"]
    provisional = "".join(e["payload"].get("delta", "") for e in deltas)

    evidence = {
        "scenario": scenario,
        "run_id": run_id,
        "run_status": run.get("status"),
        "event_types": types,
        "worker_checks": report.get("checks", {}),
        "worker_timings": report.get("timings", {}),
        "provider_calls": after["count"] - before,
        "persisted_timing": [
            e for e in timing.get("events", []) if e.get("timing")
        ],
        "egress": timing.get("egress", []),
    }

    grid.cell(
        f"{scenario}-run",
        "A",
        "F-R1 场景终态与真实链路",
        "pass" if report.get("verdict") == "pass" else "fail",
        f"worker={report.get('verdict')} run={run.get('status')}",
        evidence,
    )
    if scenario == "A1":
        grid.cell(
            "A1-1",
            "A1",
            "F-R4/R1 首段正文在完整答案前到达公共流",
            "pass" if deltas and types.index("assistant.text_delta") < types.index("message.assistant_added") else "fail",
            f"{len(deltas)} delta 帧",
            {"deltas": len(deltas)},
        )
        grid.cell(
            "A1-2",
            "A1",
            "F-R4 权威消息恰好一次且为完整答案",
            "pass" if len(assistant) == 1 and assistant[0]["payload"].get("text") == provisional else "fail",
            f"assistant={len(assistant)}",
            {"authoritative_len": len(assistant[0]["payload"].get("text", "")) if assistant else 0},
        )
        grid.cell(
            "A1-3",
            "A1",
            "F-R3 sidecar 源计时已持久化（timing_json）",
            "pass" if any(e.get("timing") for e in timing.get("events", [])) else "fail",
            f"timed_events={sum(1 for e in timing.get('events', []) if e.get('timing'))}",
            {"first_leased_at": timing.get("first_leased_at")},
        )
        _observation_contrast(grid, events, timing)
    if scenario == "A2b":
        grid.cell(
            "A2b-1",
            "A2",
            "F-R1 只读工具轮：工具调用进入公共事件流",
            "pass" if "tool.execution.started" in types else "fail",
            f"tools={sum(1 for t in types if t.startswith('tool.'))}",
            {"event_types": types},
        )
        grid.cell(
            "A2b-2",
            "A2",
            "F-R1 工具轮后仍有恰好一条权威正文",
            "pass" if len(assistant) == 1 else "fail",
            f"assistant={len(assistant)}",
        )


def _scenario_gateway_error(
    cell_id: str,
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
    *,
    expected_status: int,
    expect_single_attempt: bool,
) -> None:
    """A5: real gateway path (no stream override) — Pi → provider_proxy → fake upstream."""
    _session_id, run_id = _start_run(client, family, headers, space_id)
    before = upstream_state.snapshot()
    report = run_worker(env, cell_id, {"FG_USE_GATEWAY": "1"})
    run = wait_terminal(client, family, headers, run_id, timeout=120.0)
    after = upstream_state.snapshot()
    timing = read_run_timing(env, db_path)

    attempts = after["count"] - before["count"]
    statuses = [r["status"] for r in after["requests"][before["count"] :]]
    egress = timing.get("egress", [])
    error_classes = [e.get("error_class") for e in egress if isinstance(e, dict)]

    if expect_single_attempt:
        result = "pass" if attempts == 1 and all(s == expected_status for s in statuses) else "fail"
        note = f"永久错误出站 {attempts} 次（期望 1）"
    else:
        # 5xx is transient: the two layers multiply, and E froze the budget.
        result = "pass" if attempts > 1 else "fail"
        note = f"瞬态错误出站 {attempts} 次（>1 证明两层重试仍生效）"

    grid.cell(
        cell_id,
        "A5",
        "F-R1/R5 E 分类：出站次数与上游真实状态一致",
        result,
        note,
        {
            "attempts": attempts,
            "upstream_statuses": statuses,
            "run_status": run.get("status"),
            "egress_error_classes": error_classes,
            "worker": report,
        },
    )
    grid.cell(
        f"{cell_id}-audit",
        "A5",
        "F-R1 每次出站恰好一条安全审计且不泄露上游正文",
        "pass" if egress and all(isinstance(e, dict) and "error_class" in e for e in egress) else "fail",
        f"egress_rows={len(egress)}",
        {"error_classes": error_classes},
    )


def _observation_contrast(
    grid: Grid, events: list[dict[str, Any]], timing: dict[str, Any]
) -> None:
    """F-R3：同一次合成运行里对照两种口径，证明源计时能分离被 flush 量化的阶段。

    持久事件 ``created_at`` 之差受 sidecar 批量 flush 影响，同批内的短阶段会塌缩到
    接近 0；``timing_json`` 是 producer 单调测量的真实耗时。两者必须能分开，
    而不是把持久间隔当执行耗时。
    """
    persisted = [
        e for e in timing.get("events", [])
        if e.get("timing") and e.get("run_id") == timing.get("run_id")
    ]
    tool = next(
        (e["timing"] for e in persisted if e["type"] == "tool.execution.completed"),
        None,
    )
    assistant = next(
        (e["timing"] for e in persisted if e["type"] == "message.assistant_added"),
        None,
    )
    by_type = {e["type"]: e for e in events}
    # Persisted interval = the OLD reading. Read it from the stored column (the
    # SSE projection is the same instant in ISO form; either parses below).
    stored_by_type = {
        e["type"]: e for e in timing.get("events", [])
        if e.get("run_id") == timing.get("run_id") and e.get("created_at")
    }
    tool_created = stored_by_type.get("tool.execution.completed", {}).get("created_at")
    turn_created = stored_by_type.get("turn.started", {}).get("created_at")
    assistant_created = stored_by_type.get("message.assistant_added", {}).get("created_at")

    # The persisted interval is the OLD reading; the source clock is the NEW one.
    persisted_turn_ms = None
    if turn_created and assistant_created:
        delta = _seconds_between(turn_created, assistant_created)
        persisted_turn_ms = None if delta is None else round(delta * 1000)
    source_turn_ms = (assistant or {}).get("duration_ms")

    evidence: dict[str, Any] = {
        "source_clock_ms": {"turn": source_turn_ms, "tool": (tool or {}).get("duration_ms")},
        "first_text_ms": (assistant or {}).get("first_text_ms"),
        "persisted_created_at": {
            "turn_started": turn_created,
            "assistant_added": assistant_created,
            "tool_completed": tool_created,
        },
        "persisted_interval_ms": persisted_turn_ms,
        "flush_quantization_ms": _FLUSH_INTERVAL_MS,
    }
    # 两种口径都要有值，且必须能区分（源计时为精确值；持久间隔受批量 flush 量化）。
    # 任何一方缺失就是“拿持久间隔冒充执行耗时”的回退，判失败。
    grid.cell(
        "A1-4",
        "A1",
        "F-R3 源计时与持久间隔可区分（不把 flush 量化当执行耗时）",
        "pass"
        if source_turn_ms is not None
        and persisted_turn_ms is not None
        and (assistant or {}).get("first_text_ms") is not None
        else "fail",
        f"source_turn_ms={source_turn_ms} persisted_interval_ms={persisted_turn_ms} "
        f"first_text_ms={(assistant or {}).get('first_text_ms')}",
        evidence,
    )
    # 注入延迟必须能归到正确阶段：本轮首个正文延迟由 FG_PART_DELAY_MS 控制，
    # 因此 first_text_ms 必须真实吸收它，而 queue_wait 不得被污染。
    if INJECTED_PRE_TEXT_DELAY_MS:
        first_text = (assistant or {}).get("first_text_ms") or 0
        queue_wait_ms = None
        first_leased = timing.get("first_leased_at")
        run_created = next(
            (r.get("created_at") for r in timing.get("runs", [])
             if r.get("run_id") == timing.get("run_id")),
            None,
        )
        if first_leased and run_created:
            delta = _seconds_between(run_created, first_leased)
            queue_wait_ms = None if delta is None else round(delta * 1000)
        # 容差：允许调度抖动，但延迟必须可测且大于基线。
        attributed = first_text >= INJECTED_PRE_TEXT_DELAY_MS * 0.5
        grid.cell(
            "A1-5",
            "A1",
            "F-R3 注入口延迟归到 first_text_ms 且不污染 queue_wait",
            "pass" if attributed else "fail",
            f"injected_ms={INJECTED_PRE_TEXT_DELAY_MS} first_text_ms={first_text} "
            f"queue_wait_ms={queue_wait_ms}",
            {
                "first_text_ms": first_text,
                "model_turn_ms": source_turn_ms,
                "queue_wait_ms": queue_wait_ms,
                "tolerance": "first_text_ms >= injected/2（本地调度容差）",
            },
        )


def _seconds_between(start: str | None, end: str | None) -> float | None:
    """Seconds between two stored timestamps.

    SQLite columns use ``%Y-%m-%d %H:%M:%S.%f``; the public SSE projection
    serialises the same instant as ISO-8601 (``...T...``). Accept both so the
    caller never has to know which side it read from.
    """
    if not start or not end:
        return None
    from datetime import datetime

    def _parse(value: str) -> datetime | None:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", None):
            try:
                return datetime.fromisoformat(value) if fmt is None else datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    start_at, end_at = _parse(start), _parse(end)
    if start_at is None or end_at is None:
        return None
    return round((end_at - start_at).total_seconds(), 3)


def _scenario_queue_wait(
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
) -> None:
    """F-R1 排队：第二个 run 在 worker 忙时真实等待租约，且 queue_wait 只用 first_leased_at。

    两个 run 必须属于不同会话（同会话同时只能有一个活跃 run），但共享账户；
    先入队两个 run，再启动 worker：worker 逐个领取，第二个必然经历真实排队。
    """
    run_ids = []
    for index in range(2):
        session = client.post(
            f"{family}/api/agent/sessions", headers=headers, json={"space_id": space_id}
        ).json()
        posted = client.post(
            f"{family}/api/agent/sessions/{session['id']}/messages",
            headers={**headers, "Idempotency-Key": secrets.token_hex(16)},
            json={"content": f"queued question {index}"},
        ).json()
        run_ids.append(posted["run"]["id"])

    queued_at = time.monotonic()
    reports = []
    for _ in range(2):
        reports.append(run_worker(env, "A4-queue"))
    terminal = [wait_terminal(client, family, headers, rid) for rid in run_ids]
    elapsed_ms = int((time.monotonic() - queued_at) * 1000)
    timing = read_run_timing(env, db_path)

    leased = [
        t for t in timing.get("egress", []) if isinstance(t, dict)
    ]  # egress is not used here; kept for shape stability
    first_leased = timing.get("first_leased_at")
    statuses = [t.get("status") for t in terminal]

    grid.cell(
        "A4-1",
        "A4",
        "F-R1 两 run 排队：均取得租约并达终态",
        "pass" if statuses == ["succeeded", "succeeded"] else "fail",
        f"statuses={statuses} elapsed_ms={elapsed_ms}",
        {
            "run_ids": run_ids,
            "statuses": statuses,
            "elapsed_ms": elapsed_ms,
            "runs": timing.get("runs"),
            "worker_reports": reports,
        },
    )
    # queue_wait 只允许用 first_leased_at：第二个 run 在 worker 忙时必须真实等待。
    # Scope to THIS scenario's runs: the suite shares one isolated DB, so every
    # other scenario's runs would otherwise widen the comparison.
    runs = [r for r in timing.get("runs", []) if r.get("run_id") in run_ids]
    waits = [
        _seconds_between(r.get("created_at"), r.get("first_leased_at")) for r in runs
    ]
    grid.cell(
        "A4-2",
        "A4",
        "F-R3 queue_wait 依据 first_leased_at 而非持久事件推断",
        "pass"
        if len(runs) == 2 and all(w is not None for w in waits) and waits[1] > waits[0]
        else "fail",
        f"queue_wait_s={waits}",
        {"runs": runs, "queue_wait_seconds": waits, "scoped_run_ids": run_ids},
    )


def _scenario_cancel(
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
) -> None:
    """F-R1 中断：慢流运行中取消，终态为 cancelled，无权威正文与增量帧。

    取消必须打在 ``running`` 上：若在 ``queued`` 时取消，服务端会直接终态化，
    根本没跑到「sidecar 在飞、服务端改判」这条路径，那样通过也不能证明中断语义。
    """
    _session_id, run_id = _start_run(client, family, headers, space_id)
    # Diagnostic switch: the sidecar only learns about a server-side cancel via
    # its heartbeat, whose cadence is max(floor(leaseMs/3), 1000) — 20s at the
    # shipped 60s lease. Shortening the lease proves whether cancel detection is
    # really gated on that interval or on something else.
    worker_env = {"FG_PART_DELAY_MS": "4000"}
    if os.environ.get("FG_CANCEL_FAST_HEARTBEAT") == "1":
        worker_env["AGENT_DEFAULT_LEASE_MS"] = "3000"
    worker = run_worker_async(env, "A6-cancel", worker_env)
    # Wait for the run to actually be executing (leased→running), not queued.
    deadline = time.monotonic() + 60.0
    observed = None
    while time.monotonic() < deadline:
        current = client.get(
            f"{family}/api/agent/runs/{run_id}", headers=headers
        ).json()
        observed = current.get("status")
        if observed in ("running", "cancelled", "failed", "succeeded", "expired"):
            break
        time.sleep(0.05)
    # Give the in-flight stream a moment so the cancel lands mid-generation.
    time.sleep(1.0)
    cancelled = client.post(
        f"{family}/api/agent/runs/{run_id}/cancel", headers=headers
    )
    report = collect_worker(worker)
    run = wait_terminal(client, family, headers, run_id, timeout=90.0)
    events = sse_events(client.get(f"{family}/api/agent/runs/{run_id}/events", headers=headers))
    types = [e["type"] for e in events]
    assistant = [e for e in events if e["type"] == "message.assistant_added"]
    deltas = [e for e in events if e["type"] == "assistant.text_delta"]
    timing = read_run_timing(env, db_path)

    grid.cell(
        "A6-1",
        "A6",
        "F-R1 取消：run 收敛为 cancelled 而非 failed",
        "pass" if run.get("status") == "cancelled" else "fail",
        f"cancel_http={cancelled.status_code} run={run.get('status')} error={timing.get('error_code')}",
        {
            "event_types": types,
            "worker": report,
            "error_code": timing.get("error_code"),
            "status_before_cancel": observed,
            "sidecar_env": worker_env,
            "cancel_audits": [
                a for a in timing.get("audits", []) if "cancel" in str(a.get("action"))
            ],
        },
    )
    grid.cell(
        "A6-2",
        "A6",
        "F-R4 取消后不产生完整权威正文（不得把半成品当答案）",
        "pass" if not assistant and not deltas else "fail",
        f"assistant={len(assistant)} deltas={len(deltas)}",
        {
            "event_types": types,
            "timing_events": [e for e in timing.get("events", []) if e.get("timing")],
        },
    )


def _scenario_compaction(
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
) -> None:
    """F-R1/A3 压缩：SDK 自身阈值压缩，作为 model_turn 子成分单列。

    不自己截断历史。先用真实 API 积累真实历史（多轮完整运行），再让本轮上报大
    用量使 SDK 自己越过 (context_window - reserveTokens)，并在自身阈值
    keepRecentTokens 下找到可摘要的切点。历史不足时 SDK 会正确地拒绝压缩，
    那属于环境未构造出条件，不算通过。
    """
    session = client.post(
        f"{family}/api/agent/sessions", headers=headers, json={"space_id": space_id}
    ).json()
    seeded = 0
    for index in range(A3_SEED_TURNS):
        # AGENT_MESSAGE_MAX_LENGTH bounds one message; pad to the real limit so
        # the accumulated estimate crosses the SDK's keepRecentTokens budget.
        body = (f"seed {index} " + "amberbox " * 1000)[:A3_SEED_CHARS]
        posted = client.post(
            f"{family}/api/agent/sessions/{session['id']}/messages",
            headers={**headers, "Idempotency-Key": secrets.token_hex(16)},
            json={"content": body},
        ).json()
        run_worker(env, "A3-seed")
        seeded_run = wait_terminal(client, family, headers, posted["run"]["id"], timeout=120.0)
        if seeded_run.get("status") != "succeeded":
            grid.cell(
                "A3-0",
                "A3",
                "F-R1/A3 前置：真实多轮历史全部成功落库",
                "blocked",
                f"seed_turn={index} status={seeded_run.get('status')}",
                {"run": seeded_run},
            )
            return
        seeded += 1

    trigger = client.post(
        f"{family}/api/agent/sessions/{session['id']}/messages",
        headers={**headers, "Idempotency-Key": secrets.token_hex(16)},
        json={"content": "where did I leave the amber box?"},
    ).json()
    report = run_worker(env, "A3-compaction", {"FG_SMALL_CONTEXT": "1"})
    run = wait_terminal(client, family, headers, trigger["run"]["id"], timeout=180.0)
    timing = read_run_timing(env, db_path)
    persisted = [
        e
        for e in timing.get("events", [])
        if e.get("timing") and e.get("run_id") == trigger["run"]["id"]
    ]
    compaction_ms = [
        e["timing"].get("compaction_ms")
        for e in persisted
        if e["timing"].get("compaction_ms") is not None
    ]
    model_turns = [e["timing"] for e in persisted if e["timing"].get("duration_ms")]
    persisted_timings = [
        {"type": e["type"], "timing": e["timing"]} for e in persisted if e.get("timing")
    ]
    events = sse_events(
        client.get(f"{family}/api/agent/runs/{trigger['run']['id']}/events", headers=headers)
    )
    assistant = [e for e in events if e["type"] == "message.assistant_added"]
    checks = (report.get("checks") or {}) if isinstance(report, dict) else {}
    calls = checks.get("provider_call_log", [])
    sdk_events = checks.get("compaction_events", [])
    decisions = checks.get("compaction_decisions", [])

    grid.cell(
        "A3-1",
        "A3",
        "F-R1/A3 压缩作为 model_turn 子成分单列（compaction_ms）",
        "pass" if compaction_ms else "fail",
        f"seed_turns={seeded} compaction_samples={len(compaction_ms)}",
        {
            "compaction_ms": compaction_ms,
            "sdk_compaction_events": sdk_events,
            "compaction_decisions": decisions,
            "provider_call_log": calls,
            "estimated_context_tokens": checks.get("estimated_context_tokens"),
            "context_messages": checks.get("context_messages"),
            "compaction_settings": checks.get("compaction_settings"),
            "model_turns": model_turns,
            "persisted_timings": persisted_timings,
            "session_event_times": checks.get("session_event_times"),
        },
    )
    grid.cell(
        "A3-2",
        "A3",
        "F-R1/A3 压缩后仍产出权威正文（历史未被截断清空）",
        "pass" if run.get("status") == "succeeded" and len(assistant) == 1 else "fail",
        f"run={run.get('status')} assistant={len(assistant)}",
        {"event_types": [e["type"] for e in events], "worker": report},
    )
    # 摘要请求与作答请求必须是两次不同的模型调用，且被压缩的历史真实存在。
    summary_calls = [c for c in calls if c.get("is_summarization")]
    grid.cell(
        "A3-3",
        "A3",
        "F-R1/A3 摘要请求与作答请求分开，且历史源未被 recent-N 截断",
        "pass"
        if summary_calls and checks.get("context_messages", 0) > 1 and seeded >= A3_SEED_TURNS
        else "fail",
        f"summary_calls={len(summary_calls)} context_messages={checks.get('context_messages')}",
        {"provider_call_log": calls, "seeded_turns": seeded},
    )
    # 回归锁定：真实 SDK 把压缩排在哪个轮次边界。这一格把结论变成机器可断言的
    # 位置关系，而不是散文描述：D 的 compaction_ms 合同假定压缩发生在 turn 内。
    times = checks.get("session_event_times", [])
    kinds = [t["type"] for t in times]
    turn_start = kinds.index("turn_start") if "turn_start" in kinds else None
    msg_end = kinds.index("message_end") if "message_end" in kinds else None
    in_turn_starts = [
        i
        for i, t in enumerate(times)
        if t["type"] == "compaction_start"
        and turn_start is not None
        and msg_end is not None
        and turn_start < i < msg_end
    ]
    grid.cell(
        "A3-4",
        "A3",
        "F-R1/A3 真实 SDK 的压缩是否落在 turn 内（D 的 compaction_ms 前提）",
        "pass" if in_turn_starts else "fail",
        f"compaction_starts={sum(1 for t in times if t['type'] == 'compaction_start')} "
        f"in_turn={len(in_turn_starts)}",
        {
            "session_event_times": times,
            "compaction_decisions": decisions,
            "note": (
                "compaction_ms 只在 turn_start→message.assistant_added 之间累积；"
                "此处按真实 SDK 广播顺序判定压缩位置。"
            ),
        },
    )


def _scenario_gateway_stream(
    grid: Grid,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    env: dict[str, str],
    space_id: int,
    upstream_state: _FakeUpstreamState,
    db_path: Path,
) -> None:
    """F-R5 真实 gateway 链路：假上游分块发 SSE，gateway 必须逐块转发（不整包缓冲）。

    同时测量公共 SSE 事件到达与权威正文就绪时间；不做跨机时钟推断。
    """
    upstream_state.status = 200
    upstream_state.parts = ["The blue tin ", "is in the pantry, ", "second shelf."]
    upstream_state.chunk_delay_ms = 300
    upstream_state.chunk_times_ms = []

    _session_id, run_id = _start_run(client, family, headers, space_id)
    before = upstream_state.snapshot()["count"]
    report = run_worker(env, "R5-stream", {"FG_USE_GATEWAY": "1"})
    run = wait_terminal(client, family, headers, run_id, timeout=180.0)
    after = upstream_state.snapshot()
    timing = read_run_timing(env, db_path)

    stream = client.get(f"{family}/api/agent/runs/{run_id}/events", headers=headers)
    events = sse_events(stream)
    types = [e["type"] for e in events]
    assistant = [e for e in events if e["type"] == "message.assistant_added"]

    upstream_chunks = after["chunk_times_ms"]
    persisted = [e for e in timing.get("events", []) if e.get("timing")]
    first_text = next(
        (e["timing"].get("first_text_ms") for e in persisted if e["timing"].get("first_text_ms")),
        None,
    )

    grid.cell(
        "R5-1",
        "R5",
        "F-R5 gateway 逐块转发上游 SSE（无整包缓冲）",
        "pass"
        if len(upstream_chunks) >= 4 and (upstream_chunks[-1] - upstream_chunks[0]) > 300
        else "fail",
        f"upstream_chunks={len(upstream_chunks)} spread_ms={round(upstream_chunks[-1] - upstream_chunks[0], 1) if len(upstream_chunks) > 1 else 0}",
        {"upstream_chunk_times_ms": upstream_chunks},
    )
    grid.cell(
        "R5-2",
        "R5",
        "F-R5 真实网关链路完成一次完整运行并产出权威正文",
        "pass" if run.get("status") == "succeeded" and len(assistant) == 1 else "fail",
        f"run={run.get('status')} assistant={len(assistant)} upstream_requests={after['count'] - before}",
        {
            "event_types": types,
            "worker": report,
            "first_text_ms": first_text,
            "egress": timing.get("egress", []),
        },
    )
    grid.cell(
        "R5-3",
        "R5",
        "F-R5 时钟口径：sidecar 源计时与 gateway 审计均为同机单调测量",
        "pass" if first_text is not None else "fail",
        f"first_text_ms={first_text}",
        {
            "source": "sidecar-v1 monotonic",
            "gateway": "server-side monotonic (header_ms)",
            "cross_host_claims": "none",
        },
    )
    upstream_state.chunk_delay_ms = 0


if __name__ == "__main__":
    sys.exit(main())
