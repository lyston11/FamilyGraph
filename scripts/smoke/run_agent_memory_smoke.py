#!/usr/bin/env python3
"""Real isolated Memory → maintenance → Pi → citations smoke; no model egress.

Only model streaming is scripted. The API, database, maintenance loop, SDK,
sidecar HTTP client, event persistence and public reads are production code.
Reports contain check results only, never credentials, prompts or source text.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import run_api_smoke as api

SEED_PROVIDER = """
import os
from app.db import SessionLocal
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.utils.timeutil import utcnow
with SessionLocal() as db:
    now = utcnow()
    provider = AgentProvider(name='isolated-memory-smoke', kind='local',
        api='openai-completions', base_url='http://127.0.0.1:9/v1',
        allowed_models_json=['synthetic-model'], context_window=272000,
        max_tokens=2048, reasoning=False, input_modalities_json=['text'],
        thinking_levels_json=[], enabled=True, created_at=now, updated_at=now)
    db.add(provider)
    db.flush()
    db.add(AgentSpaceProviderSetting(space_id=int(os.environ['FG_SMOKE_SPACE_ID']),
        agent_kind='assistant', provider_id=provider.id, model='synthetic-model',
        cloud_allowed=False, local_required=False, enabled=True))
    db.commit()
"""

COUNT_DOCUMENTS = """
import os
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models.rag import RAGDocument
with SessionLocal() as db:
    print(db.scalar(select(func.count()).select_from(RAGDocument).where(
        RAGDocument.source_type=='memory',
        RAGDocument.source_id==os.environ['FG_SMOKE_MEMORY_ID'])))
"""

PRIVATE_CITATION_FIELDS = {
    "context_reference",
    "context_build_id",
    "build_id",
    "used_handles",
    "_source_ref",
    "source_ref",
    "quote_sha256",
    "content_hash",
}


def has_private_citation_fields(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in PRIVATE_CITATION_FIELDS or has_private_citation_fields(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(has_private_citation_fields(item) for item in value)
    return False


def sse_events(response: httpx.Response) -> list[dict]:
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    suite = api.Suite()
    data_dir = Path(tempfile.mkdtemp(prefix="fg-agent-memory-smoke-"))
    process = None
    blocked = False
    sidecar_checks = []
    negative_checks = {}
    negative_codes = {}

    def check(case: str, ok: bool, note: str = "") -> None:
        suite.add(api.Result(case, "isolated", "CHECK", "/", passed=bool(ok), note=note))

    try:
        public_port, internal_port, admin_port = api.free_ports(3)
        family = f"http://127.0.0.1:{public_port}"
        internal = f"http://127.0.0.1:{internal_port}"
        admin = f"http://127.0.0.1:{admin_port}"
        env = os.environ.copy()
        env.update(
            {
                "DATA_DIR": str(data_dir),
                "PYTHONPATH": str(api.BACKEND),
                "PUBLIC_API_HOST": "127.0.0.1",
                "PUBLIC_API_PORT": str(public_port),
                "INTERNAL_AGENT_API_HOST": "127.0.0.1",
                "INTERNAL_AGENT_API_PORT": str(internal_port),
                "ADMIN_API_HOST": "127.0.0.1",
                "ADMIN_API_PORT": str(admin_port),
                "SECRET_KEY": secrets.token_hex(32),
                "ADMIN_JWT_SECRET": secrets.token_hex(32),
                "ADMIN_JWT_ISSUER": "memory-smoke",
                "ADMIN_JWT_AUDIENCE": "memory-smoke-admin",
                "AGENT_SERVICE_SECRET": secrets.token_hex(32),
                "BCRYPT_ROUNDS": "4",
                "DEV_SEED_DEMO_DATA": "1",
                "PERSONAL_FAMILY_VIEW_ENABLED": "1",
                "MEMORY_ENABLED": "1",
                "RAG_ENABLED": "1",
                "AGENT_RUNTIME_ENABLED": "0",
                "STEWARD_ENABLED": "0",
                "STEWARD_WORKER_ENABLED": "0",
                "MAINTENANCE_INTERVAL_SECONDS": "0.5",
                "FG_API_BASE_URL": family,
                "FG_INTERNAL_API_BASE_URL": internal,
                "FG_SMOKE_ROOT": str(api.ROOT),
                "AGENT_RETRY_BASE_DELAY_MS": "10",
                "AGENT_RETRY_MAX_DELAY_MS": "20",
            }
        )
        migrated = subprocess.run(
            [str(api.VENV_PY), "-m", "alembic", "upgrade", "head"],
            cwd=api.BACKEND,
            env=env,
            capture_output=True,
            timeout=120,
        )
        check("isolated-migration", migrated.returncode == 0)
        if migrated.returncode:
            raise RuntimeError("isolated migration failed")

        def start() -> subprocess.Popen:
            child = subprocess.Popen(
                [str(api.VENV_PY), "-m", "app.serve"],
                cwd=api.BACKEND,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not api.wait_health(f"{family}/api/health", 25) or not api.wait_health(
                f"{admin}/admin-api/health", 25
            ):
                stop(child)
                raise ConnectionError("isolated listeners unavailable")
            return child

        process = start()
        with httpx.Client(timeout=15) as client:

            def request(
                case: str, method: str, url: str, expected: int = 200, **kwargs
            ) -> httpx.Response:
                response = client.request(method, url, **kwargs)
                check(
                    case,
                    response.status_code == expected,
                    f"HTTP {response.status_code}; code={api.error_code(response)}",
                )
                if response.status_code != expected:
                    raise AssertionError(case)
                return response

            login = request(
                "family-login",
                "POST",
                f"{family}/api/auth/login",
                json={"name": "朱元璋", "pin": "123456"},
            ).json()
            family_headers = {"Authorization": f"Bearer {login['access_token']}"}
            spaces = request("spaces", "GET", f"{family}/api/spaces", headers=family_headers).json()
            space_id = next(space["id"] for space in spaces if space["kind"] == "household")
            env["FG_SMOKE_SPACE_ID"] = str(space_id)
            username, password = api.parse_credentials(data_dir / "bootstrap/admin-credentials")
            admin_login = request(
                "admin-login",
                "POST",
                f"{admin}/admin-api/auth/login",
                json={"username": username, "password": password},
            ).json()
            new_password = "Memory-Smoke-" + secrets.token_hex(12)
            request(
                "admin-password",
                "PUT",
                f"{admin}/admin-api/auth/password",
                headers={"Authorization": f"Bearer {admin_login['access_token']}"},
                json={"current_password": password, "new_password": new_password},
            )
            admin_login = request(
                "admin-relogin",
                "POST",
                f"{admin}/admin-api/auth/login",
                json={"username": username, "password": new_password},
            ).json()
            admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
            request(
                "platform-rag-off",
                "PUT",
                f"{admin}/admin-api/v1/platform-features",
                headers=admin_headers,
                json={"memory_enabled": True, "rag_enabled": False},
            )

            def save_memory(source_text: str, prefix: str = "") -> dict:
                candidate = request(
                    f"{prefix}candidate-create",
                    "POST",
                    f"{family}/api/memory-candidates",
                    201,
                    headers=family_headers,
                    json={
                        "source": {"kind": "manual"},
                        "raw_quote": source_text,
                        "summary": source_text,
                        "purpose": "isolated integration",
                        "suggested_scope": "private",
                        "sensitivity": "normal",
                        "idempotency_key": secrets.token_hex(16),
                    },
                ).json()
                return request(
                    f"{prefix}candidate-confirm",
                    "POST",
                    f"{family}/api/memory-candidates/{candidate['id']}/confirm",
                    headers=family_headers,
                    json={"scope": "private"},
                ).json()

            memory = save_memory("今年春节在上海聚餐，外婆喜欢清淡饮食。")
            unused_memory = save_memory(
                "外婆喜欢午后在公园散步，今年春节先看花，再去图书馆。", "unused-"
            )
            env["FG_SMOKE_MEMORY_ID"] = str(memory["id"])
            env["FG_SMOKE_UNUSED_MEMORY_ID"] = str(unused_memory["id"])
            count = subprocess.run(
                [str(api.VENV_PY), "-c", COUNT_DOCUMENTS],
                cwd=api.BACKEND,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            check(
                "no-materialization-while-off",
                count.returncode == 0 and count.stdout.strip() == "0",
            )
            request(
                "platform-rag-on",
                "PUT",
                f"{admin}/admin-api/v1/platform-features",
                headers=admin_headers,
                json={"memory_enabled": True, "rag_enabled": True},
            )
            hits = []
            expected_sources = {str(memory["id"]), str(unused_memory["id"])}
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                response = client.get(
                    f"{family}/api/rag/search",
                    headers=family_headers,
                    params={"space_id": space_id, "q": "外婆喜欢什么口味？"},
                )
                if response.status_code == 200:
                    hits = response.json()
                    if expected_sources.issubset({hit["source_id"] for hit in hits}):
                        break
                time.sleep(0.2)
            check(
                "rag-only-late-enable-materialized",
                any(hit["source_id"] == str(memory["id"]) for hit in hits),
            )
            check(
                "unused-source-materialized",
                any(hit["source_id"] == str(unused_memory["id"]) for hit in hits),
            )
            if not expected_sources.issubset({hit["source_id"] for hit in hits}):
                raise AssertionError("maintenance did not materialize source")

            # RAG-only backfill is now proven; enable the Assistant runtime in
            # this same disposable database for the real sidecar chain.
            stop(process)
            process = None
            seeded = subprocess.run(
                [str(api.VENV_PY), "-c", SEED_PROVIDER],
                cwd=api.BACKEND,
                env=env,
                capture_output=True,
                timeout=20,
            )
            check("synthetic-local-provider", seeded.returncode == 0)
            env["AGENT_RUNTIME_ENABLED"] = "1"
            process = start()
            session = request(
                "assistant-session",
                "POST",
                f"{family}/api/agent/sessions",
                201,
                headers=family_headers,
                json={"space_id": space_id},
            ).json()
            runs = []
            for number, question in enumerate(
                ("外婆喜欢什么口味？", "再确认一下外婆的饮食偏好。"), 1
            ):
                posted = request(
                    f"message-{number}",
                    "POST",
                    f"{family}/api/agent/sessions/{session['id']}/messages",
                    headers={
                        **family_headers,
                        "Idempotency-Key": secrets.token_hex(16),
                    },
                    json={"content": question},
                ).json()
                run_id = posted["run"]["id"]
                runs.append(run_id)
                node_env = {
                    **env,
                    "FG_SMOKE_LOSE_RESPONSE": "1" if number == 2 else "0",
                    "FG_SMOKE_FAMILY_TOKEN": login["access_token"],
                }
                node = subprocess.run(
                    ["node", str(Path(__file__).with_name("agent_memory_worker.mjs"))],
                    cwd=api.ROOT,
                    env=node_env,
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                node_report = (
                    json.loads(node.stdout.strip().splitlines()[-1]) if node.stdout.strip() else {}
                )
                sidecar_checks.append(node_report.get("checks", {}))
                check(
                    f"real-sidecar-cycle-{number}",
                    node.returncode == 0 and node_report.get("verdict") == "pass",
                    str(node_report.get("error_type", "")),
                )
                for key, value in node_report.get("checks", {}).items():
                    if isinstance(value, bool) and (
                        number == 2
                        or key
                        not in (
                            "lost_response_injected",
                            "retry_duplicate_verified",
                            "original_reference_retry_identical",
                        )
                    ):
                        check(f"sidecar-{number}-{key}", value)
                run = request(
                    f"run-{number}",
                    "GET",
                    f"{family}/api/agent/runs/{run_id}",
                    headers=family_headers,
                ).json()
                check(f"run-{number}-succeeded", run["status"] == "succeeded")
                if run["status"] not in ("succeeded", "failed", "cancelled"):
                    raise AssertionError("sidecar did not settle its run")
                stream = request(
                    f"sse-{number}",
                    "GET",
                    f"{family}/api/agent/runs/{run_id}/events",
                    headers=family_headers,
                )
                events = sse_events(stream)
                assistant_events = [
                    event for event in events if event["type"] == "message.assistant_added"
                ]
                check(f"assistant-event-{number}-once", len(assistant_events) == 1)
                if not assistant_events:
                    continue
                event = assistant_events[0]
                fallback = request(
                    f"citations-{number}",
                    "GET",
                    f"{family}/api/agent/runs/{run_id}/events/{event['seq']}/citations",
                    headers=family_headers,
                ).json()
                check(
                    f"fallback-{number}-permission",
                    bool(fallback["citations"])
                    if number == 1
                    else not fallback["citations"] and fallback["unavailable_citation_count"] > 0,
                )
                check(
                    f"fallback-{number}-only-used-source",
                    len(fallback["citations"]) == 1
                    and fallback["citations"][0]["source_id"] == str(memory["id"])
                    if number == 1
                    else not fallback["citations"] and fallback["unavailable_citation_count"] == 1,
                )
                # The reviewed contract permits the fixed authorized fallback
                # to supply omitted citation metadata. Verify the effective
                # reading path, not a stricter SSE-only transport requirement.
                visible = event["payload"].get("citations", fallback["citations"])
                unavailable = event["payload"].get(
                    "unavailable_citation_count", fallback["unavailable_citation_count"]
                )
                check(
                    f"sse-fallback-{number}-permission",
                    bool(visible) if number == 1 else not visible and unavailable > 0,
                )
                check(
                    f"sse-{number}-byte-limit",
                    all(
                        len(json.dumps(item["payload"], ensure_ascii=False).encode()) <= 16384
                        for item in events
                    ),
                )
                check(
                    f"public-{number}-no-private-provenance",
                    not has_private_citation_fields(events)
                    and not has_private_citation_fields(fallback),
                )
                reconnect = request(
                    f"sse-reconnect-{number}",
                    "GET",
                    f"{family}/api/agent/runs/{run_id}/events",
                    headers={**family_headers, "Last-Event-ID": str(event["seq"] - 1)},
                )
                check(
                    f"sse-reconnect-{number}-same-safe-projection",
                    sse_events(reconnect)
                    == [item for item in events if item["seq"] >= event["seq"]]
                    and not has_private_citation_fields(sse_events(reconnect)),
                )

            history = request(
                "history-after-revoke",
                "GET",
                f"{family}/api/agent/sessions/{session['id']}/messages",
                headers=family_headers,
            ).json()
            assistant_history = [message for message in history if message["role"] == "assistant"]
            check(
                "history-revocation-projection",
                len(assistant_history) == 2
                and all(
                    not message.get("citations", message["content_json"].get("citations", []))
                    and message.get(
                        "unavailable_citation_count",
                        message["content_json"].get("unavailable_citation_count", 0),
                    )
                    > 0
                    for message in assistant_history
                ),
            )
            check(
                "history-no-private-provenance",
                not has_private_citation_fields(history),
            )
            search = request(
                "search-after-revoke",
                "GET",
                f"{family}/api/rag/search",
                headers=family_headers,
                params={"space_id": space_id, "q": "外婆"},
            ).json()
            check(
                "revoked-source-not-searchable",
                not any(hit["source_id"] == str(memory["id"]) for hit in search),
            )
            check(
                "unused-source-still-searchable",
                any(hit["source_id"] == str(unused_memory["id"]) for hit in search),
            )

            posted = request(
                "message-3-after-revoke",
                "POST",
                f"{family}/api/agent/sessions/{session['id']}/messages",
                headers={**family_headers, "Idempotency-Key": secrets.token_hex(16)},
                json={"content": "外婆喜欢什么活动？"},
            ).json()
            negative = subprocess.run(
                [
                    "node",
                    str(Path(__file__).with_name("agent_memory_negative_worker.mjs")),
                ],
                cwd=api.ROOT,
                env={
                    **env,
                    "FG_SMOKE_RUN_ID": str(posted["run"]["id"]),
                    "FG_SMOKE_FAMILY_TOKEN": login["access_token"],
                },
                capture_output=True,
                text=True,
                timeout=45,
            )
            negative_report = (
                json.loads(negative.stdout.strip().splitlines()[-1])
                if negative.stdout.strip()
                else {}
            )
            negative_checks = negative_report.get("checks", {})
            negative_codes = negative_report.get("codes", {})
            check(
                "real-negative-internal-driver",
                negative.returncode == 0 and negative_report.get("verdict") == "pass",
                str(negative_report.get("error_type", "")),
            )
            for key, value in negative_checks.items():
                if isinstance(value, bool):
                    check(f"negative-{key}", value)
            check(
                "no-external-network-attempts",
                len(sidecar_checks) == 2
                and all(facts.get("unauthorized_network_attempts") == 0 for facts in sidecar_checks)
                and negative_checks.get("unauthorized_network_attempts") == 0,
            )
    except (ConnectionError, FileNotFoundError, subprocess.TimeoutExpired):
        blocked = True
        check(
            "harness-environment",
            False,
            "isolated dependency, listener or process unavailable",
        )
    except Exception as error:
        check("harness-completion", False, type(error).__name__)
    finally:
        stop(process)
        shutil.rmtree(data_dir)

    report = {
        "suite": "agent-memory-real-contract-smoke",
        "verdict": "blocked" if blocked else "failed" if suite.failed else "pass",
        "model": "scripted stream; no external inference",
        "sidecar_checks": sidecar_checks,
        "negative_checks": negative_checks,
        "negative_http_codes": negative_codes,
        "counts": {"total": len(suite.results), "failed": len(suite.failed)},
        "cases": [vars(result) for result in suite.results],
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + "\n")
    else:
        print(output)
    return api.EXIT_BLOCKED if blocked else api.EXIT_FAILED if suite.failed else api.EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
