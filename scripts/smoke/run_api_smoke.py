#!/usr/bin/env python3
"""FamilyGraph 真实 API smoke（09-11 frontend-system-admin-audit-remediation Phase B）。

以隔离环境启动真实三 listener（app.serve）：
- 随机可用端口（三个 listener 均使用动态端口，不触碰开发端口占用）；
- 一次性临时 DATA_DIR（mkdtemp），运行结束整体删除，绝不触碰业务库或用户 .env；
- DEV_SEED_DEMO_DATA=1 合成演示数据（王德海家等，PIN 为公开 dev 演示值）；
- 独立随机 SECRET_KEY / ADMIN_JWT_SECRET（不同签发域，支撑交叉拒绝用例）。

用例覆盖（PRD R2/R3/R7）：
- 家庭登录/刷新、bootstrap、空间列表、家庭卡、PFV、统计、通知读写、记忆、
  Agent 会话与 SSE 终态、附件（上传/授权 raw/删除/无权安全失败）；
- 后台登录/刷新/me/概览/空间详情/治理写入；
- 认证边界：家庭 token 打后台 listener、admin token 打家庭 listener 一律拒绝；
  家庭 listener 上 /admin-api/* 与随机未知路由 404 字节一致。

报告合同：只输出 {id, listener, method, path, status, code, ms, note}——
path 为模板（不含 query），响应体不进报告；禁止姓名、PIN、JWT、Cookie、
prompt、provider 原始响应与本地路径。运行结束删除临时 DATA_DIR 与
bootstrap 凭据（凭据只存活于子进程生命周期内）。

退出码：0=全部通过；1=有真实失败；2=环境阻塞（端口无法分配/后端无法启动）。
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
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"

EXIT_PASS = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

TIMEOUT = httpx.Timeout(15.0)


def free_ports(count: int) -> list[int]:
    # Hold every socket until all ports are allocated so the OS cannot return
    # the same ephemeral port for two listeners in this process.
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [int(sock.getsockname()[1]) for sock in sockets]


@dataclass
class Result:
    id: str
    listener: str
    method: str
    path: str
    status: int | None = None
    code: str | None = None
    ms: int | None = None
    note: str = ""
    passed: bool | None = None


@dataclass
class Suite:
    results: list[Result] = field(default_factory=list)

    def add(self, result: Result) -> None:
        self.results.append(result)

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if r.passed is False]


def error_code(resp: httpx.Response) -> str | None:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应按无错误码处理
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        return code if isinstance(code, str) else None
    return None


def record(
    suite: Suite,
    case_id: str,
    listener: str,
    method: str,
    path: str,
    response: httpx.Response | None,
    started: float,
    expect_status: int,
    expect_code: str | None = None,
    note: str = "",
) -> None:
    status = response.status_code if response is not None else None
    code = error_code(response) if response is not None else None
    ok = status == expect_status and (expect_code is None or code == expect_code)
    suite.add(
        Result(
            id=case_id,
            listener=listener,
            method=method,
            path=path,
            status=status,
            code=code,
            ms=int((time.monotonic() - started) * 1000),
            note=note if ok else f"{note}; expected status={expect_status} code={expect_code}",
            passed=ok,
        )
    )


def wait_health(url: str, deadline_s: float = 60.0) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def parse_credentials(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    username = re.search(r"username:\s*(\S+)", text)
    password = re.search(r"password:\s*(\S+)", text)
    if not username or not password:
        raise ValueError("admin credentials file format unexpected")
    return username.group(1), password.group(1)


def run_memory_cases(
    suite: Suite,
    client: httpx.Client,
    family: str,
    headers: dict[str, str],
    space_id: int,
) -> None:
    """Exercise real memory writes and source revocation in the synthetic DB."""

    def request(
        case_id: str,
        method: str,
        path: str,
        expected_status: int,
        *,
        route: str | None = None,
        payload: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        started = time.monotonic()
        response = client.request(
            method, f"{family}/api{path}", headers=headers, json=payload, params=params
        )
        record(
            suite, f"family-memory-{case_id}", "family", method, route or path,
            response, started, expected_status,
        )
        return response

    def check(case_id: str, passed: bool, route: str, note: str) -> bool:
        suite.add(Result(
            f"family-memory-{case_id}", "family", "CHECK", route,
            note=note, passed=passed,
        ))
        return passed

    original_text = "Smoke memory contract: the keepsake is in the amber box."
    payload: dict[str, object] = {
        "source": {"kind": "manual"},
        "raw_quote": original_text,
        "summary": original_text,
        "suggested_scope": "private",
        "purpose": "isolated smoke verification",
        "sensitivity": "normal",
        "idempotency_key": secrets.token_hex(16),
    }
    request(
        "missing-source", "POST", "/memory-candidates", 422,
        payload={key: value for key, value in payload.items() if key != "source"},
    )
    response = request("create", "POST", "/memory-candidates", 201, payload=payload)
    if response.status_code != 201:
        return
    candidate = response.json()
    candidate_id = candidate.get("id") if isinstance(candidate, dict) else None
    if not check(
        "candidate-contract",
        isinstance(candidate_id, int)
        and candidate.get("raw_quote") == original_text
        and candidate.get("source_kind") == "manual"
        and candidate.get("source_status") == "available",
        "/memory-candidates", "explicit source and raw_quote projection",
    ):
        return
    response = request("create-retry", "POST", "/memory-candidates", 201, payload=payload)
    check(
        "create-idempotent", response.status_code == 201
        and response.json().get("id") == candidate_id,
        "/memory-candidates", "same request returns original candidate",
    )
    request(
        "key-conflict", "POST", "/memory-candidates", 409,
        payload={**payload, "summary": "A different requested summary."},
    )
    response = request("pending", "GET", "/memory-candidates", 200)
    check(
        "pending-visible", response.status_code == 200
        and any(item.get("id") == candidate_id for item in response.json()),
        "/memory-candidates", "candidate list serializes the committed row",
    )
    response = request(
        "pending-not-indexed", "GET", "/rag/search", 200,
        params={"space_id": space_id, "q": "amber box"},
    )
    check(
        "pending-no-hits", response.status_code == 200 and response.json() == [],
        "/rag/search", "unconfirmed candidate is not searchable",
    )
    confirm_path = f"/memory-candidates/{candidate_id}/confirm"
    confirmation: dict[str, object] = {"scope": "private", "retention_days": 7}
    response = request(
        "confirm", "POST", confirm_path, 200,
        route="/memory-candidates/{id}/confirm", payload=confirmation,
    )
    if response.status_code != 200:
        return
    memory = response.json()
    memory_id = memory.get("id") if isinstance(memory, dict) else None
    if not check(
        "confirmed-contract", isinstance(memory_id, int)
        and memory.get("raw_quote") == original_text,
        "/memory-candidates/{id}/confirm", "confirmation returns a readable memory",
    ):
        return
    response = request(
        "confirm-retry", "POST", confirm_path, 200,
        route="/memory-candidates/{id}/confirm", payload=confirmation,
    )
    check(
        "confirm-idempotent", response.status_code == 200
        and response.json().get("id") == memory_id
        and response.json().get("retention_until") == memory.get("retention_until"),
        "/memory-candidates/{id}/confirm", "retry preserves memory and retention",
    )
    response = request(
        "search-confirmed", "GET", "/rag/search", 200,
        params={"space_id": space_id, "q": "amber box"},
    )
    hits = response.json() if response.status_code == 200 else []
    hit = next((item for item in hits if item.get("source_id") == str(memory_id)), None)
    if not check(
        "confirmed-search-hit", hit is not None,
        "/rag/search", "confirmed memory supplies a versioned source",
    ):
        return
    assert hit is not None
    copy_text = "Smoke copied memory contract remains source dependent."
    response = request(
        "rag-save", "POST", "/memory-candidates", 201,
        payload={
            "source": {
                "kind": "rag_chunk", "document_id": hit["document_id"],
                "chunk_id": hit["chunk_id"], "revision": hit["revision"],
                "index_version": hit["index_version"], "space_id": space_id,
            },
            "summary": copy_text,
            "suggested_scope": "private",
            "purpose": "isolated source dependency verification",
            "sensitivity": "normal",
            "idempotency_key": secrets.token_hex(16),
        },
    )
    if response.status_code != 201:
        return
    copy_candidate_id = response.json()["id"]
    response = request(
        "rag-confirm", "POST", f"/memory-candidates/{copy_candidate_id}/confirm", 200,
        route="/memory-candidates/{id}/confirm", payload={"scope": "private"},
    )
    if response.status_code != 200:
        return
    copy_memory_id = response.json()["id"]
    request(
        "revoke-source", "POST", f"/memories/{memory_id}/revoke", 200,
        route="/memories/{id}/revoke",
    )
    response = request("source-dependent-list", "GET", "/memories", 200)
    memories = response.json() if response.status_code == 200 else []
    copied = next((item for item in memories if item.get("id") == copy_memory_id), None)
    check(
        "source-revocation-redacts-copy", copied is not None
        and copied.get("source_status") == "unavailable"
        and copied.get("raw_quote") is None and copied.get("content") is None,
        "/memories", "owner metadata remains while source-dependent text is hidden",
    )
    for case_id, query in (("original-no-longer-indexed", "amber box"),
                           ("copy-no-longer-indexed", "copied memory contract")):
        response = request(
            case_id, "GET", "/rag/search", 200,
            params={"space_id": space_id, "q": query},
        )
        check(
            f"{case_id}-empty", response.status_code == 200 and response.json() == [],
            "/rag/search", "revoked source and its copy are not retrievable",
        )
    request(
        "delete-copy", "DELETE", f"/memories/{copy_memory_id}", 204,
        route="/memories/{id}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=None, help="脱敏 JSON 报告输出路径")
    args = parser.parse_args()

    if not VENV_PY.exists():
        print("BLOCKED: backend/.venv 不存在，无法启动真实 listener", file=sys.stderr)
        return EXIT_BLOCKED

    data_dir = Path(tempfile.mkdtemp(prefix="fg-smoke-"))
    public_port, internal_port, admin_port = free_ports(3)
    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            # A linked dependency environment may contain an editable install
            # of another checkout. Every app subprocess must use this source.
            "PYTHONPATH": str(BACKEND),
            "PUBLIC_API_PORT": str(public_port),
            "PUBLIC_API_HOST": "127.0.0.1",
            "INTERNAL_AGENT_API_PORT": str(internal_port),
            "INTERNAL_AGENT_API_HOST": "127.0.0.1",
            "ADMIN_API_PORT": str(admin_port),
            "ADMIN_API_HOST": "127.0.0.1",
            "SECRET_KEY": secrets.token_hex(32),
            "ADMIN_JWT_SECRET": secrets.token_hex(32),
            "ADMIN_JWT_ISSUER": "familygraph-smoke-admin-issuer",
            "ADMIN_JWT_AUDIENCE": "familygraph-smoke-admin-audience",
            "DEV_SEED_DEMO_DATA": "1",
            "BCRYPT_ROUNDS": "4",
            "AGENT_RUNTIME_ENABLED": "1",
            "AGENT_SERVICE_SECRET": secrets.token_hex(32),
            "PERSONAL_FAMILY_VIEW_ENABLED": "1",
            "MEMORY_ENABLED": "1",
            "RAG_ENABLED": "1",
            "STEWARD_ENABLED": "",
            "STEWARD_WORKER_ENABLED": "",
        }
    )

    family = f"http://127.0.0.1:{public_port}"
    admin = f"http://127.0.0.1:{admin_port}"
    suite = Suite()
    process: subprocess.Popen | None = None

    try:
        # 隔离库先完成迁移（lifespan 拒绝在未迁移数据库上 bootstrap）
        migrate = subprocess.run(
            [str(VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if migrate.returncode != 0:
            print("BLOCKED: alembic upgrade head 失败", file=sys.stderr)
            print(migrate.stderr[-2000:], file=sys.stderr)
            return EXIT_BLOCKED

        process = subprocess.Popen(
            [str(VENV_PY), "-m", "app.serve"],
            cwd=str(BACKEND),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_health(f"{family}/api/health") or not wait_health(f"{admin}/admin-api/health"):
            print("BLOCKED: 真实 listener 未能在 60s 内就绪", file=sys.stderr)
            return EXIT_BLOCKED

        run_cases(suite, family, admin, data_dir)
    except Exception as exc:  # noqa: BLE001 - harness 自身异常视为环境阻塞
        print(f"BLOCKED: smoke harness 异常：{type(exc).__name__}", file=sys.stderr)
        return EXIT_BLOCKED
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(data_dir, ignore_errors=True)

    verdict = "pass" if not suite.failed else "failed"
    report = {
        "suite": "familygraph-real-api-smoke",
        "verdict": verdict,
        "listeners": {
            "family": f"127.0.0.1:{public_port}",
            "internal": f"127.0.0.1:{internal_port}",
            "admin": f"127.0.0.1:{admin_port}",
        },
        "counts": {
            "total": len(suite.results),
            "passed": len(suite.results) - len(suite.failed),
            "failed": len(suite.failed),
        },
        "cases": [vars(r) for r in suite.results],
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(output, encoding="utf-8")
    else:
        print(output)
    return EXIT_PASS if not suite.failed else EXIT_FAILED


def run_cases(suite: Suite, family: str, admin: str, data_dir: Path) -> None:
    client = httpx.Client(timeout=TIMEOUT)
    admin_access_for_cross = ""

    # ---- 家庭认证 ----
    started = time.monotonic()
    resp = client.post(
        f"{family}/api/auth/login",
        json={"name": "王德海", "pin": "123456"},
    )
    record(suite, "family-login", "family", "POST", "/auth/login", resp, started, 200)
    family_tokens = resp.json() if resp.status_code == 200 else {}
    family_access = family_tokens.get("access_token", "")

    started = time.monotonic()
    resp = client.post(
        f"{family}/api/auth/refresh",
        json={"refresh_token": family_tokens.get("refresh_token", "")},
    )
    record(suite, "family-refresh", "family", "POST", "/auth/refresh", resp, started, 200)
    family_tokens = resp.json() if resp.status_code == 200 else family_tokens
    family_access = family_tokens.get("access_token", family_access)

    fam_headers = {"Authorization": f"Bearer {family_access}"}

    started = time.monotonic()
    resp = client.get(f"{family}/api/me", headers=fam_headers)
    record(suite, "family-me", "family", "GET", "/me", resp, started, 200)

    # ---- 空间 ----
    started = time.monotonic()
    resp = client.get(f"{family}/api/spaces", headers=fam_headers)
    record(suite, "family-spaces", "family", "GET", "/spaces", resp, started, 200)
    spaces = resp.json() if resp.status_code == 200 else []
    space_id = next(
        (s.get("id") for s in spaces if isinstance(s, dict) and s.get("kind") == "household"),
        None,
    )
    if space_id is None:
        suite.add(Result("family-space-id", "family", "GET", "/spaces", note="no household space", passed=False))
        return

    started = time.monotonic()
    resp = client.get(f"{family}/api/household-card", params={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-household-card", "family", "GET", "/household-card", resp, started, 200)
    etag = resp.headers.get("etag")
    if etag:
        started = time.monotonic()
        resp304 = client.get(
            f"{family}/api/household-card",
            params={"space_id": space_id},
            headers={**fam_headers, "If-None-Match": etag},
        )
        record(suite, "family-household-card-304", "family", "GET", "/household-card", resp304, started, 304)

    started = time.monotonic()
    resp = client.get(f"{family}/api/personal-family-view", params={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-pfv", "family", "GET", "/personal-family-view", resp, started, 200)

    started = time.monotonic()
    resp = client.get(f"{family}/api/stats", params={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-stats", "family", "GET", "/stats", resp, started, 200)

    # ---- 通知读写 ----
    started = time.monotonic()
    resp = client.get(f"{family}/api/notifications", params={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-notifications", "family", "GET", "/notifications", resp, started, 200)

    started = time.monotonic()
    resp = client.post(f"{family}/api/notifications/read-all", json={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-notifications-read-all", "family", "POST", "/notifications/read-all", resp, started, 200)

    # ---- 记忆 ----
    started = time.monotonic()
    resp = client.get(f"{family}/api/memories", params={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-memories", "family", "GET", "/memories", resp, started, 200)
    run_memory_cases(suite, client, family, fam_headers, space_id)

    # ---- Agent 会话 + SSE 终态（未启用 provider：SSE 对未知 run 为统一 404 终态） ----
    started = time.monotonic()
    resp = client.post(f"{family}/api/agent/sessions", json={"space_id": space_id}, headers=fam_headers)
    record(suite, "family-agent-session", "family", "POST", "/agent/sessions", resp, started, 201, note="gateway session create")

    started = time.monotonic()
    resp = client.get(f"{family}/api/agent/runs/999999999/events", headers=fam_headers)
    record(suite, "family-agent-sse-terminal", "family", "GET", "/agent/runs/{id}/events", resp, started, 404, expect_code=None, note="SSE unknown run terminal state")

    # ---- 附件：上传（真实 PNG，Pillow 生成）→ 授权 raw 200 → 无权用户安全失败 ----
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(120, 140, 160)).save(buffer, format="PNG")
    png = buffer.getvalue()
    started = time.monotonic()
    resp = client.post(
        f"{family}/api/users/1/attachments/image",
        params={"title": "smoke"},
        files={"file": ("smoke.png", png, "image/png")},
        headers=fam_headers,
    )
    record(suite, "family-attachment-upload", "family", "POST", "/users/{id}/attachments/image", resp, started, 201)
    attachment_id = resp.json().get("id") if resp.status_code == 201 else None

    if attachment_id is not None:
        started = time.monotonic()
        resp = client.get(f"{family}/api/attachments/{attachment_id}/raw", headers=fam_headers)
        record(suite, "family-attachment-raw-authorized", "family", "GET", "/attachments/{id}/raw", resp, started, 200, note="Bearer media fetch")

        # 无权主体：全新注册账号与演示空间无任何成员关系 → 统一安全失败
        # （演示名单内所有账号彼此共享空间，不能充当无权主体）
        started = time.monotonic()
        outsider = client.post(
            f"{family}/api/auth/register",
            json={"name": f"smoke-outsider-{secrets.token_hex(3)}", "pin": "654321"},
        )
        outsider_access = outsider.json().get("access_token", "") if outsider.status_code in (200, 201) else ""
        if outsider_access:
            resp = client.get(
                f"{family}/api/attachments/{attachment_id}/raw",
                headers={"Authorization": f"Bearer {outsider_access}"},
            )
            record(suite, "family-attachment-raw-denied", "family", "GET", "/attachments/{id}/raw", resp, started, 404, note="anti-enumeration safe failure for unrelated principal")
        else:
            suite.add(Result("family-attachment-raw-denied", "family", "GET", "/attachments/{id}/raw", note=f"outsider register unavailable ({outsider.status_code})", passed=False))

        started = time.monotonic()
        resp = client.delete(f"{family}/api/attachments/{attachment_id}", headers=fam_headers)
        record(suite, "family-attachment-delete", "family", "DELETE", "/attachments/{id}", resp, started, 204)

    # ---- 后台认证与读模型 ----
    cred_path = data_dir / "bootstrap" / "admin-credentials"
    if cred_path.exists():
        username, password = parse_credentials(cred_path)
        started = time.monotonic()
        resp = client.post(f"{admin}/admin-api/auth/login", json={"username": username, "password": password})
        record(suite, "admin-login", "admin", "POST", "/admin-api/auth/login", resp, started, 200)

        # 首登改密（password_must_change）：改密后全部会话撤销，需重新登录
        admin_new_password = "Smoke-Admin-" + secrets.token_hex(12)
        admin_access = resp.json().get("access_token", "") if resp.status_code == 200 else ""
        if admin_access:
            started = time.monotonic()
            resp = client.put(
                f"{admin}/admin-api/auth/password",
                json={"current_password": password, "new_password": admin_new_password},
                headers={"Authorization": f"Bearer {admin_access}"},
            )
            record(suite, "admin-first-password-change", "admin", "PUT", "/admin-api/auth/password", resp, started, 200)
            started = time.monotonic()
            resp = client.post(
                f"{admin}/admin-api/auth/login",
                json={"username": username, "password": admin_new_password},
            )
            record(suite, "admin-relogin", "admin", "POST", "/admin-api/auth/login", resp, started, 200)

        admin_tokens = resp.json() if resp.status_code == 200 else {}
        admin_access = admin_tokens.get("access_token", "")
        adm_headers = {"Authorization": f"Bearer {admin_access}"}

        started = time.monotonic()
        resp = client.post(
            f"{admin}/admin-api/auth/refresh",
            json={"refresh_token": admin_tokens.get("refresh_token", "")},
        )
        record(suite, "admin-refresh", "admin", "POST", "/admin-api/auth/refresh", resp, started, 200)
        if resp.status_code == 200:
            admin_access = resp.json().get("access_token", admin_access)
            adm_headers = {"Authorization": f"Bearer {admin_access}"}

        started = time.monotonic()
        resp = client.get(f"{admin}/admin-api/auth/me", headers=adm_headers)
        record(suite, "admin-me", "admin", "GET", "/admin-api/auth/me", resp, started, 200)

        started = time.monotonic()
        resp = client.get(f"{admin}/admin-api/v1/overview", headers=adm_headers)
        record(suite, "admin-overview", "admin", "GET", "/admin-api/v1/overview", resp, started, 200)

        started = time.monotonic()
        resp = client.get(f"{admin}/admin-api/v1/spaces/{space_id}", headers=adm_headers)
        record(suite, "admin-space-detail", "admin", "GET", "/admin-api/v1/spaces/{id}", resp, started, 200)
        admin_access_for_cross = admin_access

        # 治理写路径探针：未终止态不可改判/未知申请 → 404 统一错误外壳（写端点已挂载）
        started = time.monotonic()
        resp = client.post(
            f"{admin}/admin-api/v1/manager-applications/999999999/reject",
            json={"note": "smoke probe", "confirm": True},
            headers=adm_headers,
        )
        record(suite, "admin-governance-write-probe", "admin", "POST", "/admin-api/v1/manager-applications/{id}/reject", resp, started, 404, note="governance write endpoint mounted, unknown id -> uniform 404")
    else:
        suite.add(Result("admin-login", "admin", "POST", "/admin-api/auth/login", note="bootstrap credentials file missing", passed=False))

    # ---- 认证边界与交叉拒绝（R7） ----
    started = time.monotonic()
    resp = client.get(f"{admin}/admin-api/v1/overview", headers={"Authorization": f"Bearer {family_access}"})
    record(suite, "cross-family-token-on-admin", "admin", "GET", "/admin-api/v1/overview", resp, started, 401, note="family token must not open admin listener")

    if admin_access_for_cross:
        started = time.monotonic()
        resp = client.get(f"{family}/api/me", headers={"Authorization": f"Bearer {admin_access_for_cross}"})
        record(suite, "cross-admin-token-on-family", "family", "GET", "/me", resp, started, 401, note="admin token must not open family listener")
    else:
        suite.add(Result("cross-admin-token-on-family", "family", "GET", "/me", note="admin token unavailable", passed=False))

    # 家庭 listener 上 /admin-api/* 与随机未知路由 404 一致（byte-identical 语义层）
    started = time.monotonic()
    probe_admin_path = client.get(f"{family}/admin-api/v1/overview")
    started2 = time.monotonic()
    probe_unknown = client.get(f"{family}/definitely-not-a-route-0911")
    same_shell = (
        probe_admin_path.status_code == 404
        and probe_unknown.status_code == 404
        and error_code(probe_admin_path) == error_code(probe_unknown)
        and probe_admin_path.content == probe_unknown.content
    )
    suite.add(
        Result(
            "cross-admin-path-on-family-404",
            "family",
            "GET",
            "/admin-api/*",
            status=probe_admin_path.status_code,
            code=error_code(probe_admin_path),
            ms=int((time.monotonic() - started) * 1000),
            note="byte-identical to unknown route 404" if same_shell else "404 shells differ",
            passed=same_shell,
        )
    )
    suite.add(
        Result(
            "family-unknown-route-404",
            "family",
            "GET",
            "/definitely-not-a-route-0911",
            status=probe_unknown.status_code,
            code=error_code(probe_unknown),
            ms=int((time.monotonic() - started2) * 1000),
            passed=probe_unknown.status_code == 404,
        )
    )

    # 未认证访问家庭受保护资源 → 401
    started = time.monotonic()
    resp = client.get(f"{family}/api/spaces")
    record(suite, "family-spaces-unauthenticated", "family", "GET", "/spaces", resp, started, 401)

    client.close()


if __name__ == "__main__":
    sys.exit(main())
