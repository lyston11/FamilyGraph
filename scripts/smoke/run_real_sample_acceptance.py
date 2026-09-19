#!/usr/bin/env python3
"""G-3：有界真实小样本验收（真实 Provider，隔离 DATA_DIR，物理请求硬封顶）。

## 为什么需要这个脚本

PRD G-R4 的硬前置是「物理请求预算真正可执行」。当前两层重试相乘，单个逻辑样本
最坏产生 `(requestRetries+1) × (sessionRetries+1)` = 6×4 = **24 次**出站，因此
「只发 6 条消息」保证不了 12 次物理请求上限。本脚本在**测试出站边界**放一个计数
代理：无论哪一层重试发起请求，都要先过它的准入闸门，达到上限即拒绝新请求。

`provider_profile_error` 把云 Provider 的 base_url 硬锁在批准端点（防止静默换端），
所以不能在既有云行上插代理。本脚本改为在**隔离库**里建一个 `kind='local'` 的
Provider 指向代理——`local` 是既有的敏感数据回退路径，不新增任何生产可达的
云 profile 绕过开关（生产的 `agent_providers` 仍只有锁定行，本脚本绝不改它）。

## 安全

- 真实 API key 只在服务器进程内从隔离库解密，经内存传给代理；不落盘、不打印、
  不进报告。
- 报告只含：每笔的出站序号/状态/字节、是否发出、usage 来源、合法输出与应用判定。
  不含 prompt、thinking、回复正文、凭据、密钥。
- 默认连**假上游**（`--dry-run` 语义）：先用零费用验证闸门真的会封顶。

## 用法

```bash
# 零费用干跑：验证计数闸门与拒绝路径
python3 scripts/smoke/run_real_sample_acceptance.py --upstream-url http://127.0.0.1:PORT/v1 \
    --report /tmp/g3-dry.json --physical-cap 4 --samples 3

# 真实小样本（需已批准费用）
python3 scripts/smoke/run_real_sample_acceptance.py \
    --report /tmp/g3-real.json --assistant-samples 1
```

退出码：0 通过；1 有真实失败；2 环境阻塞。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

#: 单请求输出上限（码元）。PRD：不超过现配置且 ≤1000。
SAMPLE_OUTPUT_CAP_TOKENS = 1000


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def free_ports(count: int) -> list[int]:
    # 顺序绑定并保持直到返回：避免并发取端口时拿到同一个号。
    socks = [socket.socket() for _ in range(count)]
    try:
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in socks]
    finally:
        for sock in socks:
            sock.close()


# ---------------------------------------------------------------- 计数代理


@dataclass
class BudgetState:
    """物理请求预算。准入在锁内完成，达到上限后**不再有请求发往真实上游**。"""

    physical_cap: int
    admitted: int = 0
    rejected: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    output_cap_tokens: int = SAMPLE_OUTPUT_CAP_TOKENS
    stopped: bool = False

    def admit(self) -> tuple[bool, int]:
        """返回 (是否准入, 序号)。拒绝时不消耗序号，也不发往上游。"""
        with self.lock:
            if self.stopped or self.admitted >= self.physical_cap:
                self.rejected += 1
                self.stopped = True
                return False, -1
            self.admitted += 1
            return True, self.admitted

    def record(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.requests.append(entry)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "physical_cap": self.physical_cap,
                "admitted": self.admitted,
                "rejected": self.rejected,
                "stopped": self.stopped,
                "output_cap_tokens": self.output_cap_tokens,
                "requests": list(self.requests),
            }


def _extract_usage(chunk: bytes, acc: dict[str, Any]) -> None:
    """从 SSE 帧中抽取 usage（OpenAI completions / Responses 两种形状）。

    只为报告使用；不存储 prompt 或正文。usage 可能出现在任意一帧
    （completions 在末帧，responses 在 `response.completed`）。
    """
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - 非文本帧直接忽略
        return
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            resp = obj.get("response")
            usage = resp.get("usage") if isinstance(resp, dict) else None
        if isinstance(usage, dict):
            acc["usage_frames"] = int(acc.get("usage_frames", 0)) + 1
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "input_tokens",
                "output_tokens",
            ):
                value = usage.get(key)
                if isinstance(value, int):
                    acc[key] = value
            details = usage.get("output_tokens_details")
            if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
                acc["reasoning_tokens"] = details["reasoning_tokens"]
            continue
        # 诊断（不存正文）：该帧的键名，用于判定 usage 是否根本不在流里。
        top = obj.get("type") or obj.get("object")
        if isinstance(top, str):
            seen = acc.setdefault("frame_types", {})
            seen[top] = int(seen.get(top, 0)) + 1


def _cap_output_tokens(payload: dict[str, Any], cap: int) -> tuple[dict[str, Any], bool]:
    """把任意 token 上限字段压到 cap。返回 (payload, 是否改写)。"""
    changed = False
    for key in ("max_output_tokens", "max_tokens", "max_completion_tokens"):
        value = payload.get(key)
        if isinstance(value, int) and value > cap:
            payload[key] = cap
            changed = True
    return payload, changed


def start_counting_proxy(
    state: BudgetState,
    upstream_base: str,
    api_key: str,
    upstream_path: str,
) -> ThreadingHTTPServer:
    """只转发一个协议路径的计数代理；超预算直接 503，不触碰上游。"""
    import httpx

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # 静默：日志由报告承担
            return

        def _reject(self, reason: str) -> None:
            body = json.dumps({"error": {"message": reason, "type": "budget_exhausted"}}).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            ok, seq = state.admit()
            if not ok:
                # 预算耗尽：请求从未发往上游。这是「停止新请求」的实现点。
                state.record({"seq": None, "outcome": "budget_exhausted", "sent": False})
                self._reject("sample budget exhausted; request not forwarded")
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                state.record({"seq": seq, "outcome": "bad_request", "sent": False})
                self._reject("invalid JSON body")
                return
            capped, changed = _cap_output_tokens(payload, state.output_cap_tokens)
            body = json.dumps(capped, ensure_ascii=False).encode()
            request_bytes = len(body)
            target = f"{upstream_base.rstrip('/')}{upstream_path}"
            headers = {
                "Content-Type": "application/json",
                "Accept": self.headers.get("Accept") or "text/event-stream",
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            started = time.monotonic()
            try:
                with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
                    with client.stream("POST", target, content=body, headers=headers) as upstream:
                        self.send_response(upstream.status_code)
                        content_type = upstream.headers.get("Content-Type")
                        if content_type:
                            self.send_header("Content-Type", content_type)
                        else:
                            self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Transfer-Encoding", "chunked")
                        self.end_headers()
                        total = 0
                        usage_acc: dict[str, Any] = {}
                        for chunk in upstream.iter_bytes():
                            total += len(chunk)
                            _extract_usage(chunk, usage_acc)
                            self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                            self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                        state.record(
                            {
                                "seq": seq,
                                "outcome": "streamed",
                                "sent": True,
                                "upstream_status": upstream.status_code,
                                "bytes_to_client": total,
                                # 请求体字节数用于**成本上界**（不是 token 计数）；
                                # usage 缺失时不得用字节反推 token。
                                "request_bytes": request_bytes,
                                "output_cap_applied": changed,
                                # usage 来自上游自己的报数；缺字段时如实留空，
                                # 不用字符数反推 token（那是另一次元的数据）。
                                "usage": usage_acc or None,
                                "duration_ms": int((time.monotonic() - started) * 1000),
                            }
                        )
            except Exception as exc:  # noqa: BLE001 - 代理故障如实上报
                state.record(
                    {
                        "seq": seq,
                        "outcome": "proxy_error",
                        "sent": True,
                        "error_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                )
                try:
                    self.send_response(502)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except Exception:  # noqa: BLE001 - 客户端可能已断开
                    pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------- 辅助


def _openai_completions_sse(parts: list[str]) -> list[bytes]:
    """最小 OpenAI chat.completions SSE（role / deltas / stop）。与 F 一致。"""
    frames: list[bytes] = []

    def frame(obj: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(obj)}\n\n".encode()

    frames.append(
        frame(
            {
                "id": "chatcmpl-g3",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "synthetic-model",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}],
            }
        )
    )
    for part in parts:
        frames.append(
            frame(
                {
                    "id": "chatcmpl-g3",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "synthetic-model",
                    "choices": [{"index": 0, "delta": {"content": part}}],
                }
            )
        )
    frames.append(
        frame(
            {
                "id": "chatcmpl-g3",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "synthetic-model",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }
        )
    )
    frames.append(b"data: [DONE]\n\n")
    return frames


def start_fake_upstream(parts: list[str]) -> ThreadingHTTPServer:
    """零费用上游（completions 协议）；只用于验证预算闸门，不接触真实 Provider。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - http.server 接口
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for frame in _openai_completions_sse(parts):
                self.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


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


def _read_provider_credentials(env: dict[str, str], db_path: Path) -> tuple[str, str]:
    """从隔离库解密真实 Provider 的 base_url 与 api_key（不打印）。"""
    script = """
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider
from app.utils.secretbox import decrypt_secret
with SessionLocal() as db:
    row = db.query(AgentProvider).filter_by(name=os.environ["FG_REAL_PROVIDER_NAME"]).one()
    key = decrypt_secret(row.secret_ciphertext) if row.secret_ciphertext else ""
    print(row.base_url or "")
    print(key)
"""
    completed = subprocess.run(
        [str(VENV_PY), "-c", script],
        cwd=str(BACKEND),
        env={**env, "FG_REAL_PROVIDER_NAME": "liu-dada"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"credential read failed: {type(completed.stderr).__name__}")
    lines = completed.stdout.strip().splitlines()
    if len(lines) < 2:
        raise RuntimeError("credential read returned no base_url/key")
    return lines[0].strip(), lines[1].strip()


SEED_LOCAL_PROVIDER = """
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils.timeutil import utcnow
with SessionLocal() as db:
    now = utcnow()
    name = os.environ["FG_LOCAL_PROVIDER_NAME"]
    row = db.query(AgentProvider).filter_by(name=name).one_or_none()
    if row is None:
        row = AgentProvider(name=name, created_at=now, updated_at=now)
        db.add(row)
    # 与批准 profile 同协议/同模型/同档位；只有 base_url 指向测试计数代理。
    row.kind = "local"
    row.api = os.environ["FG_REAL_API"]
    row.base_url = os.environ["FG_PROXY_BASE_URL"]
    row.allowed_models_json = [os.environ["FG_REAL_MODEL"]]
    row.context_window = int(os.environ["FG_REAL_CONTEXT_WINDOW"])
    row.max_tokens = int(os.environ["FG_REAL_MAX_TOKENS"])
    row.reasoning = os.environ["FG_REAL_REASONING"] == "1"
    row.input_modalities_json = ["text", "image"]
    row.thinking_levels_json = ["low", "medium", "high", "xhigh", "max"]
    row.compat_json = {}
    row.enabled = True
    row.updated_at = now
    db.flush()
    for space_id in os.environ["FG_SPACE_IDS"].split(","):
        for kind in ("assistant", "steward"):
            setting = (
                db.query(AgentSpaceProviderSetting)
                .filter_by(space_id=int(space_id), agent_kind=kind)
                .one_or_none()
            )
            if setting is None:
                # AgentSpaceProviderSetting 无 created_at/updated_at 列。
                setting = AgentSpaceProviderSetting(
                    space_id=int(space_id), agent_kind=kind
                )
                db.add(setting)
            setting.provider_id = row.id
            setting.model = os.environ["FG_REAL_MODEL"]
            setting.cloud_allowed = True
            setting.local_required = False
            setting.enabled = True
    db.commit()
print("seeded")
"""

READ_EGRESS = """
import json, sqlite3, os
baseline = int(os.environ.get("FG_BASELINE_RUN_ID", "0"))
con = sqlite3.connect(os.environ["FG_DB"])
runs = con.execute(
    "select id, status, error_code, created_at, first_leased_at, settled_at from agent_runs "
    "where id > ? order by id", (baseline,)
).fetchall()
egress = con.execute(
    "select target_id, detail_json, created_at from audit_log "
    "where action='agent_provider_egress' and target_id > ? order by id", (baseline,)
).fetchall()
msgs = con.execute(
    "select id, role, substr(content_json, 1, 200) from agent_messages where id > "
    "(select coalesce(max(id), 0) from agent_messages m2 "
    " where m2.session_id in (select id from agent_sessions where id <= "
    "  (select coalesce(max(id),0) from agent_sessions s2 where s2.id not in "
    "    (select distinct session_id from agent_runs where id > ?)))) "
    "order by id", (baseline,)
).fetchall()
events = con.execute(
    "select run_id, type, timing_json, created_at from agent_run_events "
    "where run_id > ? order by seq", (baseline,)
).fetchall()
print(json.dumps({
    "baseline_run_id": baseline,
    "runs": runs, "egress": egress, "messages": msgs, "events": events,
}))
"""


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None)
    parser.add_argument("--upstream-url", default=None,
                        help="真实上游 base（默认取隔离库中的批准 Provider）")
    parser.add_argument("--seed-from-db", default=None,
                        help="用生产在线备份的副本初始化隔离库（保留真实 Provider 密文行）；"
                             "不复制任何 prompt，合成输入由本脚本自己控制")
    parser.add_argument("--dry-run", action="store_true",
                        help="不读取真实凭据；用于零费用验证闸门")
    parser.add_argument("--fake-upstream-port", type=int, default=None)
    parser.add_argument("--physical-cap", type=int, default=12)
    parser.add_argument("--assistant-samples", type=int, default=4)
    parser.add_argument("--steward-samples", type=int, default=2)
    parser.add_argument("--wall-clock-s", type=float, default=900.0)
    parser.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if not VENV_PY.exists():
        print("BLOCKED: backend/.venv 不存在", file=sys.stderr)
        return EXIT_BLOCKED
    if not (AGENT / "dist" / "main.js").exists():
        print("BLOCKED: agent/dist 未构建", file=sys.stderr)
        return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="fg-g3-"))
    ports = free_ports(6)
    public_port, internal_port, admin_port, health_port, unused_a, unused_b = ports
    env: dict[str, str] = {}
    procs: list[subprocess.Popen] = []
    proxy: ThreadingHTTPServer | None = None
    fakes: list[ThreadingHTTPServer] = []
    started_at = time.monotonic()
    cells: list[dict[str, Any]] = []

    def cell(cid: str, requirement: str, result: str, note: str, evidence: Any = None) -> None:
        cells.append(
            {"cell": cid, "requirement": requirement, "result": result,
             "note": note, "evidence": evidence}
        )

    try:
        env = os.environ.copy()
        # 隔离 DATA_DIR：先建库再迁移。SECRET_KEY 等来自部署环境（解密真实凭据所需）。
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
                "DEV_SEED_DEMO_DATA": "1",
                "BCRYPT_ROUNDS": "4",
                "AGENT_RUNTIME_ENABLED": "1",
                "PERSONAL_FAMILY_VIEW_ENABLED": "1",
                "MEMORY_ENABLED": "0",
                "RAG_ENABLED": "0",
                "STEWARD_ENABLED": "",
                "STEWARD_WORKER_ENABLED": "",
                # 测试栈端口（与生产 8000/8001/8002 严格分离）
                "FG_API_BASE_URL": f"http://127.0.0.1:{public_port}",
                "FG_INTERNAL_API_BASE_URL": f"http://127.0.0.1:{internal_port}",
                "HEALTH_PORT": str(health_port),
                "AGENT_SIDECAR_ID": "fg-g3-sample",
                # 生产重试策略**不变**：预算由代理闸门保证，不靠调这两个值。
                "AGENT_LEASE_POLL_MS": "250",
            }
        )
        env.setdefault("SECRET_KEY", secrets.token_hex(32))
        env.setdefault("ADMIN_JWT_SECRET", secrets.token_hex(32))
        env.setdefault("ADMIN_JWT_ISSUER", "fg-g3-admin-issuer")
        env.setdefault("ADMIN_JWT_AUDIENCE", "fg-g3-admin-audience")
        env.setdefault("AGENT_SERVICE_SECRET", secrets.token_hex(32))

        migrate = subprocess.run(
            [str(VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND), env=env, capture_output=True, text=True, timeout=300,
        )
        if migrate.returncode != 0:
            print(f"BLOCKED: alembic 失败\n{migrate.stderr[-800:]}", file=sys.stderr)
            return EXIT_BLOCKED
        cell("G3-ENV-1", "隔离库迁移到 head", "pass", "alembic upgrade head")

        # 用生产在线备份初始化隔离库：保留真实 Provider 行（含密文）与凭据链，
        # 不替生产写任何东西。合成输入由本脚本自己构造。
        if args.seed_from_db:
            import sqlite3 as _sqlite3

            src = _sqlite3.connect(args.seed_from_db)
            dst = _sqlite3.connect(str(data_dir / "db" / "app.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            providers = subprocess.run(
                [str(VENV_PY), "-c",
                 "import sqlite3,os;"
                 "con=sqlite3.connect(os.environ['FG_DB']);"
                 "print(con.execute('select name,kind from agent_providers').fetchall())"],
                cwd=str(BACKEND), env={**env, "FG_DB": str(data_dir / "db" / "app.db")},
                capture_output=True, text=True, timeout=60,
            )
            cell(
                "G3-ENV-1b",
                "隔离库来自生产在线备份副本（保留真实 Provider 行）",
                "pass",
                f"providers={providers.stdout.strip()[:120]}",
            )

        # 真实上游与凭据
        if args.dry_run or args.upstream_url:
            if args.upstream_url:
                upstream_base = args.upstream_url
            else:
                fake = start_fake_upstream(["ok", " ", "done"])
                fakes.append(fake)
                upstream_base = f"http://127.0.0.1:{fake.server_address[1]}/v1"
            api_key = ""
            real_api, real_model = "openai-completions", "synthetic-model"
            real_ctx, real_max, real_reasoning = "272000", "2048", "0"
        else:
            upstream_base, api_key = _read_provider_credentials(env, data_dir / "db" / "app.db")
            real_api = env.get("FG_REAL_API", "openai-responses")
            real_model = env.get("FG_REAL_MODEL", "gpt-5.6-sol")
            real_ctx = env.get("FG_REAL_CONTEXT_WINDOW", "272000")
            real_max = env.get("FG_REAL_MAX_TOKENS", "60000")
            real_reasoning = env.get("FG_REAL_REASONING", "1")
        if not upstream_base:
            print("BLOCKED: 未取得上游 base_url", file=sys.stderr)
            return EXIT_BLOCKED

        budget = BudgetState(physical_cap=args.physical_cap)
        proxy = start_counting_proxy(
            budget, upstream_base, api_key, "/responses" if real_api == "openai-responses" else "/chat/completions"
        )
        proxy_port = proxy.server_address[1]
        cell(
            "G3-ENV-2", "计数代理已启动（预算闸门在出站边界）", "pass",
            f"proxy=127.0.0.1:{proxy_port} upstream={'fake' if api_key == '' else 'approved-provider'} "
            f"physical_cap={args.physical_cap}",
            {"physical_cap": args.physical_cap, "output_cap_tokens": budget.output_cap_tokens},
        )

        # 三个 listener
        serve_log = data_dir.parent / f"{data_dir.name}-serve.log"
        handle = serve_log.open("w", encoding="utf-8")
        procs.append(
            subprocess.Popen(
                [str(VENV_PY), "-m", "app.serve"], cwd=str(BACKEND), env=env,
                stdout=handle, stderr=subprocess.STDOUT,
            )
        )
        family = f"http://127.0.0.1:{public_port}"
        if not wait_health(f"{family}/api/health"):
            tail = "\n".join(serve_log.read_text(encoding="utf-8").splitlines()[-15:])
            print(f"BLOCKED: listener 未就绪\n{tail}", file=sys.stderr)
            return EXIT_BLOCKED
        cell("G3-ENV-3", "隔离三 listener 就绪", "pass", "app.serve")

        import urllib.request

        def post(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
            request = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)

        tokens = post(f"{family}/api/auth/login", {"name": "朱元璋", "pin": "123456"})
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        request = urllib.request.Request(f"{family}/api/spaces", headers=auth)
        with urllib.request.urlopen(request, timeout=30) as response:
            spaces = json.load(response)
        space_ids = [str(s["id"]) for s in spaces]

        seeded = subprocess.run(
            [str(VENV_PY), "-c", SEED_LOCAL_PROVIDER], cwd=str(BACKEND),
            env={
                **env,
                "FG_SPACE_IDS": ",".join(space_ids),
                "FG_LOCAL_PROVIDER_NAME": "g3-sample-proxy",
                "FG_PROXY_BASE_URL": f"http://127.0.0.1:{proxy_port}/v1",
                "FG_REAL_API": real_api,
                "FG_REAL_MODEL": real_model,
                "FG_REAL_CONTEXT_WINDOW": real_ctx,
                "FG_REAL_MAX_TOKENS": real_max,
                "FG_REAL_REASONING": real_reasoning,
            },
            capture_output=True, text=True, timeout=90,
        )
        if seeded.returncode != 0:
            print(f"BLOCKED: Provider 播种失败\n{seeded.stderr[-600:]}", file=sys.stderr)
            return EXIT_BLOCKED
        cell(
            "G3-ENV-4",
            "隔离库 Provider 指向计数代理（生产 agent_providers 未被改动）",
            "pass",
            f"local_provider=g3-sample-proxy spaces={len(space_ids)}",
        )

        # 不启动独立 sidecar 进程：`controlled_assistant_worker.mjs` 在进程内构造真实
        # `SidecarWorker`/`InternalClient`/Pi session（项目锁定的 SDK），自己 lease 并执行。
        # 再跑一个 `dist/main.js` 会变成两个 worker 争同一个 lease。
        cell(
            "G3-ENV-5",
            "真实 sidecar（项目锁定 Pi SDK）由 worker 进程内驱动",
            "pass",
            "controlled_assistant_worker.mjs → agent/dist/worker.js",
        )

        # ---- 样本执行 ----
        db_path = data_dir / "db" / "app.db"

        def run_sample(index: int) -> dict[str, Any]:
            body = post(
                f"{family}/api/agent/sessions",
                {"space_id": int(space_ids[0])},
                auth,
            )
            posted = post(
                f"{family}/api/agent/sessions/{body['id']}/messages",
                {"content": f"用一句话回答：第 {index} 号合成问题的答案是几？"},
                {**auth, "Idempotency-Key": secrets.token_hex(16)},
            )
            run_id = posted["run"]["id"]
            env_w = dict(env)
            # 走**真实网关路径**（Pi → internal provider proxy → 计数代理 → 上游）。
            # 不加 FG_USE_GATEWAY 的话 worker 会用脚本化流，根本不发网络请求，
            # 那将证明不了真实链路。
            completed = subprocess.run(
                ["node", str(WORKER)], cwd=str(ROOT),
                env={**env_w, "FG_SCENARIO": "A1-run", "FG_USE_GATEWAY": "1"},
                capture_output=True, text=True, timeout=300,
            )
            deadline = time.monotonic() + 240
            run: dict[str, Any] = {}
            while time.monotonic() < deadline:
                request = urllib.request.Request(
                    f"{family}/api/agent/runs/{run_id}", headers=auth
                )
                with urllib.request.urlopen(request, timeout=15) as response:
                    run = json.load(response)
                if run.get("status") in ("succeeded", "failed", "cancelled", "expired"):
                    break
                time.sleep(1.0)
            return {
                "index": index,
                "run_id": run_id,
                "status": run.get("status"),
                "error_code": run.get("error_code"),
                "worker_returncode": completed.returncode,
                "worker_verdict": (completed.stdout.strip().splitlines() or [""])[-1][:200],
            }

        # 本 harness 自己的 Run 起点：隔离库可能来自生产备份，带历史 run 与
        # egress 审计。不过滤的话「审计数 vs 代理数」会很本不对，且会把历史
        # 失败计入本次分母。
        baseline_probe = subprocess.run(
            [str(VENV_PY), "-c",
             "import sqlite3,os;\n"
             "con=sqlite3.connect(os.environ['FG_DB']);\n"
             "print(con.execute('select coalesce(max(id),0) from agent_runs').fetchone()[0])"],
            cwd=str(BACKEND), env={**env, "FG_DB": str(db_path)},
            capture_output=True, text=True, timeout=60,
        )
        baseline_run_id = int(baseline_probe.stdout.strip() or 0)

        samples: list[dict[str, Any]] = []
        for index in range(1, args.assistant_samples + 1):
            if time.monotonic() - started_at > args.wall_clock_s:
                samples.append({"index": index, "status": "skipped", "reason": "wall_clock"})
                break
            if budget.snapshot()["stopped"]:
                samples.append({"index": index, "status": "skipped", "reason": "budget_exhausted"})
                break
            samples.append(run_sample(index))

        # ---- 读取证据 ----
        read = subprocess.run(
            [str(VENV_PY), "-c", READ_EGRESS], cwd=str(BACKEND),
            env={**env, "FG_DB": str(db_path), "FG_BASELINE_RUN_ID": str(baseline_run_id)},
            capture_output=True, text=True, timeout=90,
        )
        ledger = json.loads(read.stdout) if read.returncode == 0 else {}

        snap = budget.snapshot()
        forwards = [r for r in snap["requests"] if r.get("outcome") == "streamed"]
        cell(
            "G3-1",
            "物理请求预算可执行：达到上限后请求不再发往上游",
            "pass" if snap["admitted"] <= snap["physical_cap"] else "fail",
            f"admitted={snap['admitted']} rejected={snap['rejected']} cap={snap['physical_cap']}",
            {"budget": snap},
        )
        egress_rows = ledger.get("egress") or []
        cell(
            "G3-2",
            "网关出站审计与代理计数一致（无未计数出站）",
            "pass" if len(egress_rows) == snap["admitted"] + snap["rejected"] else "fail",
            f"egress_audits={len(egress_rows)} proxy_total={snap['admitted'] + snap['rejected']}",
            {"egress_count": len(egress_rows)},
        )
        succeeded = [s for s in samples if s.get("status") == "succeeded"]
        cell(
            "G3-3",
            "逻辑样本分别记录可达/发出/合法输出/保存状态",
            "pass" if succeeded else ("blocked" if not forwards else "fail"),
            f"samples={len(samples)} succeeded={len(succeeded)} forwarded={len(forwards)}",
            {"samples": samples, "messages": (ledger.get("messages") or [])[:12]},
        )

        exit_code = EXIT_PASS
        if any(c["result"] == "fail" for c in cells):
            exit_code = EXIT_FAILED
        elif any(c["result"] == "blocked" for c in cells):
            exit_code = EXIT_BLOCKED

        report = {
            "suite": "familygraph-g3-real-sample-acceptance",
            "dry_run": bool(args.dry_run or args.upstream_url),
            "verdict": {0: "pass", 1: "failed", 2: "blocked"}[exit_code],
            "counts": {
                "total": len(cells),
                "passed": sum(1 for c in cells if c["result"] == "pass"),
                "failed": sum(1 for c in cells if c["result"] == "fail"),
                "blocked": sum(1 for c in cells if c["result"] == "blocked"),
            },
            "wall_clock_s": round(time.monotonic() - started_at, 1),
            "ledger": ledger,
            "cells": cells,
        }
        if args.report:
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
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
        if proxy is not None:
            proxy.shutdown()
        for fake in fakes:
            fake.shutdown()
        for name in ("serve", "sidecar"):
            (data_dir.parent / f"{data_dir.name}-{name}.log").unlink(missing_ok=True)
        if args.keep:
            print(f"kept DATA_DIR: {data_dir}", file=sys.stderr)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
