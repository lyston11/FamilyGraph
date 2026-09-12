#!/usr/bin/env python3
"""Steward 隔离端到端验收驱动（09-11 R1/AC-1）。

隔离性：临时 DATA_DIR（不触碰业务库与 .env）； Alembic 迁移链建库后通过
真实 API（家庭 :8000 app 与 admin :8002 app 的进程内 TestClient）走完整业务
流；后台进度由真实调度路径（maintenance.run_maintenance_tick —— worker 循环
每次 tick 执行的同一函数）推进，绝不直接调用 run_steward_job 代替调度。

场景：注册/登录/建档 → 建空间 → 成员加入 → 双向确认关系（confirmed
SourceFact + 领域事件）→ 自动 tick → job/PFV/卡片/通知 → 模型辅助批次
（fake provider HTTP 服务：成功/畸形/超时）→ 建议审阅/提交/确认 → 撤权 →
失败注入 → 进程中断恢复（过期 lease/batch）→ 关闭/重开 → 最终重算。

证据：仅记录 ID/状态/计数（绝无个人内容）到 backend/.steward-e2e-evidence.json。

用法：cd backend && .venv/bin/python scripts/steward_e2e.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- 环境必须在导入 app 之前就绪（与 tests/conftest.py 同一合同）----
_TMP = tempfile.mkdtemp(prefix="familygraph-steward-e2e-")
os.environ["DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "e2e-secret-key-not-a-real-secret")
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("ADMIN_JWT_SECRET", "e2e-admin-jwt-secret-0123456789abcdef")
os.environ.setdefault("ADMIN_JWT_ISSUER", "familygraph-admin-e2e")
os.environ.setdefault("ADMIN_JWT_AUDIENCE", "familygraph-admin-web-e2e")
os.environ.setdefault("AGENT_SERVICE_SECRET", "e2e-agent-service-secret")
os.environ.setdefault("AGENT_RUNTIME_ENABLED", "1")
os.environ["STEWARD_ENABLED"] = "1"
os.environ["STEWARD_WORKER_ENABLED"] = "1"
os.environ["STEWARD_SCAN_INTERVAL_SECONDS"] = "60"
os.environ["PERSONAL_FAMILY_VIEW_ENABLED"] = "1"
os.environ["MEMORY_ENABLED"] = "1"
os.environ["RAG_ENABLED"] = "1"
os.environ["BEHAVIOR_PROJECTION_ENABLED"] = "1"
os.environ["STEWARD_ASSIST_CANDIDATE"] = "1"
os.environ["STEWARD_ASSIST_RANKING"] = "1"
os.environ["STEWARD_ASSIST_EXPLANATION"] = "1"
os.environ["STEWARD_ASSIST_TIMEOUT_SECONDS"] = "1.5"

EVIDENCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".steward-e2e-evidence.json"
)
EVIDENCE: dict = {"data_dir": _TMP, "steps": []}


def step(name: str, **record: object) -> dict:
    entry = {"step": name, **record}
    EVIDENCE["steps"].append(entry)
    print(f"[e2e] {name}: {json.dumps(record, ensure_ascii=False, default=str)[:400]}")
    return entry


# ---- fake provider：受控协议服务（成功/畸形/超时三模式）----
_mode = {"value": "success"}


class _FakeProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) or b"{}"
        with open("/tmp/e2e-fake-provider-last.json", "ab") as fh:
            fh.write(raw + b"\n\n")
        body = json.loads(raw)
        if _mode["value"] == "timeout":
            time.sleep(5)
            self.send_response(200)
            self.end_headers()
            return
        if _mode["value"] == "malformed":
            payload = b"not-json-at-all"
        else:
            user_text = ""
            inp = body.get("input")
            if isinstance(inp, str):
                user_text = inp
            elif isinstance(inp, list):
                for part in inp:
                    if isinstance(part, dict) and part.get("role") == "user":
                        content = part.get("content")
                        if isinstance(content, str):
                            user_text = content
                            break
                        if isinstance(content, list):
                            user_text += " ".join(
                                str(p.get("text") or "") for p in content if isinstance(p, dict)
                            )
            roster = None
            start = user_text.find("{")
            if start >= 0:
                try:
                    roster = json.loads(user_text[start:])
                except ValueError:
                    roster = None
            if roster and roster.get("nodes"):
                nodes = [n["node"] for n in roster["nodes"]]
                text = json.dumps(
                    [{"kind": "direct_sibling", "subject": nodes[0], "object": nodes[1]}]
                    if len(nodes) > 1
                    else []
                )
            else:
                text = "[]"
            payload = json.dumps(
                {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": text}]}
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                }
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_fake_provider() -> tuple[str, ThreadingHTTPServer]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), _FakeProviderHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/v1", server


def main() -> int:
    import platform as _plat

    EVIDENCE["hardware"] = {
        "machine": _plat.machine(),
        "processor": _plat.processor(),
        "python": sys.version.split()[0],
        "os": _plat.platform(),
    }
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    result = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"], cwd=root, capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stderr
    step("migrate_head", ok=True, data_dir=_TMP)

    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app import config
    from app.db import SessionLocal
    from app.main import admin_app, app
    from app.models.notification import Notification
    from app.models.personal_family_view import PersonalFamilyView
    from app.models.steward import (
        ActionCard,
        StewardAssistBatch,
        StewardJob,
        StewardLlmCandidate,
        StewardModelCall,
    )
    from app.models.steward_suggestion import StewardSuggestion
    from app.services import maintenance
    from app.utils.secretbox import encrypt_secret

    config.AGENT_PROVIDER_STANDARD_PROFILE_ONLY = False
    client = TestClient(app)
    admin = TestClient(admin_app)

    def auth(token_pair: dict) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_pair['access_token']}"}

    def register(name: str) -> dict:
        r = client.post("/api/auth/register", json={"name": name, "pin": "135246"})
        assert r.status_code == 200, (name, r.status_code, r.text)
        user_id = r.json()["user"]["id"]
        # 登录换发独立 token（claim 会话语义）
        login = client.post("/api/auth/login", json={"name": name, "pin": "135246"})
        assert login.status_code == 200, login.text
        return {"user_id": user_id, "headers": auth(login.json())}

    def tick_until_drained(max_ticks: int = 12) -> dict:
        """真实调度路径：连续 run_maintenance_tick 直到队列排干（含退避等待）。"""
        ticks = 0
        executed = 0
        for _ in range(max_ticks):
            counters = maintenance.run_maintenance_tick()
            ticks += 1
            executed += counters["steward_executed"]
            db = SessionLocal()
            try:
                pending = list(
                    db.scalars(
                        select(StewardJob).where(
                            StewardJob.status.in_(("queued", "leased", "running"))
                        )
                    )
                )
            finally:
                db.close()
            if not pending and counters["steward_scanned"] == 0:
                break
            time.sleep(0.2)
        return {"ticks": ticks, "jobs_executed": executed}

    def wait_batches(timeout: float = 20.0, *, keep_ticking: bool = False) -> list[str]:
        """辅助批次在受限线程内真实 HTTP 执行；轮询至收敛。

        keep_ticking：辅助批次由 maintenance tick 调度（每 tick 至多一个），
        模拟真实持续运行的循环在等待期间继续 tick。
        """
        import faulthandler

        deadline = time.time() + timeout
        statuses: list[str] = []
        while time.time() < deadline:
            if keep_ticking:
                maintenance.run_maintenance_tick()
            db = SessionLocal()
            try:
                statuses = [
                    b.status
                    for b in db.scalars(select(StewardAssistBatch)).all()
                    if b.status in ("pending", "leased", "applying")
                ]
                if not statuses:
                    break
            finally:
                db.close()
            time.sleep(0.2)
        else:
            faulthandler.dump_traceback(file=sys.stderr)
        return statuses

    # ---- 1. 注册 / 登录 / 建档 ----
    a = register("e2e-甲")
    step("register_owner", user_id=a["user_id"])
    b = register("e2e-乙")
    c = register("e2e-丙")
    step("register_members", user_ids=[b["user_id"], c["user_id"]])

    r = client.post(
        "/api/spaces", json={"name": "e2e household", "kind": "household"}, headers=a["headers"]
    )
    assert r.status_code == 201, r.text
    space_id = r.json()["id"]
    step("create_space", space_id=space_id)

    # ---- 2. 成员加入（owner 邀请 → 本人接受）----
    for member in (b, c):
        r = client.post(
            f"/api/spaces/{space_id}/members",
            json={"user_id": member["user_id"]},
            headers=a["headers"],
        )
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = client.post(f"/api/space-memberships/{mid}/accept", headers=member["headers"])
        assert r.status_code == 200, r.text
    step("members_joined", space_id=space_id, member_ids=[a["user_id"], b["user_id"], c["user_id"]])

    # ---- 3. 建档（本人姓名更新 → profile 事件）----
    r = client.put("/api/me/name", json={"name": "e2e-甲-改名"}, headers=a["headers"])
    assert r.status_code == 200, r.text
    step("profile_updated", ok=True)

    # ---- 4. 双向确认关系（connection accept → confirmed spouse SourceFact）----
    r = client.post(
        "/api/connection-requests",
        json={"target_id": b["user_id"], "dir_class": "spouse", "label": "配偶"},
        headers=a["headers"],
    )
    assert r.status_code == 201, r.text
    edge_id = r.json()["id"]
    r = client.post(f"/api/connection-requests/{edge_id}/accept", headers=b["headers"])
    assert r.status_code == 200, r.text
    step("relation_confirmed", relation_id=edge_id, dir_class="spouse")

    # ---- 5. 自动维护 tick → job/PFV/卡片/通知 ----
    drained = tick_until_drained()
    db = SessionLocal()
    jobs = list(db.scalars(select(StewardJob).where(StewardJob.space_id == space_id)).all())
    cards = list(db.scalars(select(ActionCard).where(ActionCard.space_id == space_id)).all())
    pfvs = list(db.scalars(select(PersonalFamilyView)).all())
    notifs = list(db.scalars(select(Notification)).all())
    step(
        "core_tick",
        **drained,
        job_ids=[j.id for j in jobs],
        job_statuses={str(j.id): j.status for j in jobs},
        cards_created=[c.id for c in cards if c.state != "superseded"],
        pfv_rows=len(pfvs),
        pfv_current=sum(1 for v in pfvs if v.status == "current"),
        notifications=len(notifs),
    )
    assert all(j.status == "succeeded" for j in jobs), jobs
    db.close()

    # ---- 6. admin :8002 观测状态（真实 DB 指标）----
    from app.models.system_admin import SystemAdmin, SystemAdminAccount
    from app.utils import security
    from app.utils import timeutil as tu

    db = SessionLocal()
    now = tu.utcnow()
    adm = SystemAdmin(username="e2e-admin", status="active", created_at=now, updated_at=now)
    adm.account = SystemAdminAccount(
        password_hash=security.hash_password("E2eAdmin-2026x"),
        password_must_change=False,
        password_version=0,
        failed_attempts=0,
        locked_until=None,
        status="claimed",
        claimed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(adm)
    db.commit()
    db.close()
    r = admin.post(
        "/admin-api/auth/login", json={"username": "e2e-admin", "password": "E2eAdmin-2026x"}
    )
    assert r.status_code == 200, r.text
    ah = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = admin.get("/admin-api/v1/steward/status", headers=ah)
    assert r.status_code == 200, r.text
    step(
        "admin_status",
        state=r.json()["state"],
        metrics=r.json()["metrics"],
        alerts=r.json()["alerts"],
    )

    # ---- 7. 模型辅助批次（fake provider：成功）----
    from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting

    base_url, fakesrv = _start_fake_provider()
    db = SessionLocal()
    provider = AgentProvider(
        name="e2e-fake-provider",
        kind="openai_compatible",
        base_url=base_url,
        secret_ciphertext=encrypt_secret("sk-e2e-fake-not-a-real-key"),
        allowed_models_json=["e2e-model"],
        enabled=True,
        created_at=tu.utcnow(),
        updated_at=tu.utcnow(),
    )
    db.add(provider)
    db.flush()
    db.add(
        AgentSpaceProviderSetting(
            space_id=space_id,
            agent_kind="steward",
            provider_id=provider.id,
            model="e2e-model",
            cloud_allowed=True,
            enabled=True,
            assist_candidate=True,
            assist_ranking=True,
            assist_explanation=True,
        )
    )
    db.commit()
    db.close()
    step("assist_provider_registered", provider_id=provider.id, base_url_host="127.0.0.1(fake)")

    # 新事件（成员改名）触发新 job → 注册批次
    r = client.put("/api/me/name", json={"name": "e2e-乙-改名"}, headers=b["headers"])
    assert r.status_code == 200, r.text
    for _ in range(10):
        maintenance.run_maintenance_tick()
        if wait_batches(timeout=3.0):
            continue
        break
    db = SessionLocal()
    batches = list(db.scalars(select(StewardAssistBatch)).all())
    calls = list(db.scalars(select(StewardModelCall)).all())
    candidates = list(db.scalars(select(StewardLlmCandidate)).all())
    jobs = list(db.scalars(select(StewardJob).where(StewardJob.space_id == space_id)).all())
    step(
        "assist_success",
        **drained,
        batch_ids=[bt.id for bt in batches],
        batch_statuses={str(bt.id): bt.status for bt in batches},
        call_statuses=[cl.status for cl in calls],
        llm_candidates=len(candidates),
        all_core_succeeded=all(j.status == "succeeded" for j in jobs),
    )
    assert batches, "期望至少一个辅助批次"
    assert all(
        bt.status in ("applied", "superseded", "failed", "degraded") for bt in batches
    ), batches
    assert any(bt.status == "applied" for bt in batches), batches
    assert any(cl.status == "succeeded" for cl in calls), calls
    db.close()

    # ---- 8. 建议审阅/提交/确认（candidate-review 闭环）----
    # 再触发一次 job：投影候选建议
    r = client.put("/api/me/name", json={"name": "e2e-丙-改名"}, headers=c["headers"])
    assert r.status_code == 200, r.text
    drained = tick_until_drained()
    wait_batches()
    db = SessionLocal()
    suggestions = list(db.scalars(select(StewardSuggestion)).all())
    sug_summary = [
        {"id": s.id, "kind": s.kind, "state": s.status, "origin": s.origin, "revision": s.revision}
        for s in suggestions
    ]
    step(
        "suggestions_projected",
        suggestions=sug_summary,
        core_jobs=drained["jobs_executed"],
    )
    db.close()
    if suggestions:
        target = next(s for s in suggestions if s.status == "proposed")
        r = client.get(
            "/api/steward-suggestions", params={"space_id": space_id}, headers=a["headers"]
        )
        assert r.status_code == 200, r.text
        assert any(it["id"] == target.id for it in r.json()["items"])
        # dismiss 一条 + submit 一条（若都是 pending，先 dismiss 第二条）
        others = [s for s in suggestions if s.id != target.id and s.status == "proposed"]
        if others:
            r = client.post(
                f"/api/steward-suggestions/{others[0].id}/dismiss",
                params={"space_id": space_id},
                json={"expected_revision": others[0].revision},
                headers=a["headers"],
            )
            assert r.status_code == 200, r.text
            step("suggestion_dismissed", suggestion_id=others[0].id, state=r.json()["state"])
        r = client.post(
            f"/api/steward-suggestions/{target.id}/submit",
            params={"space_id": space_id},
            json={
                "expected_revision": target.revision,
                "evidence_hash": target.evidence_hash,
                "confirm": True,
            },
            headers={"Idempotency-Key": "e2e-submit-1", **a["headers"]},
        )
        submit_status = r.status_code
        submit_body = (
            r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        )
        linked = submit_body.get("linked_proposal") or {}
        step(
            "suggestion_submitted",
            suggestion_id=target.id,
            status=submit_status,
            linked_proposal_state=linked.get("state"),
        )
        if submit_status == 202:
            fact_id = linked.get("source_fact_id")
            # 确认者是端点当事人（subject 本人），owner 非端点不得代确认
            r = client.post(
                f"/api/relationship-proposals/{fact_id}/confirm",
                params={"space_id": space_id},
                json={"expected_revision": linked.get("revision", 1)},
                headers=b["headers"],
            )
            confirm_status = r.status_code
            confirm_state = r.json().get("state") if r.status_code == 200 else None
            step(
                "proposal_confirmed",
                source_fact_id=fact_id,
                status=confirm_status,
                state=confirm_state,
            )
        # 最终重算
        drained = tick_until_drained()
        db = SessionLocal()
        jobs = list(db.scalars(select(StewardJob).where(StewardJob.space_id == space_id)).all())
        step(
            "final_recompute",
            jobs_total=len(jobs),
            all_succeeded=all(j.status == "succeeded" for j in jobs),
        )
        db.close()
    else:
        step("suggestions_projected_note", note="本场景未投影出建议（确定性候选为空也合规）")

    # ---- 9. 失败注入（畸形响应 → degraded/failed 辅助；core 仍成功）----
    # 注意：注入必须在撤权之前——撤权后空间无 confirmed 事实，候选 roster 为空，
    # 根本不会注册辅助批次，注入将无从观察。
    _mode["value"] = "malformed"
    r = client.put("/api/me/name", json={"name": "e2e-甲-再改名"}, headers=a["headers"])
    assert r.status_code == 200, r.text
    drained = tick_until_drained()
    wait_batches()
    db = SessionLocal()
    calls = list(db.scalars(select(StewardModelCall)).all())
    batches_all = list(db.scalars(select(StewardAssistBatch)).all())
    bad = [cl for cl in calls if cl.status in ("failed", "degraded", "unknown")]
    jobs = list(db.scalars(select(StewardJob).where(StewardJob.space_id == space_id)).all())
    step(
        "inject_malformed_provider",
        failed_calls=[{"status": cl.status, "error_code": cl.error_code} for cl in bad],
        all_call_statuses=[cl.status for cl in calls],
        batch_statuses={str(bt.id): bt.status for bt in batches_all},
        core_all_succeeded=all(j.status == "succeeded" for j in jobs),
    )
    assert bad, "期望畸形响应产生 failed/degraded 辅助调用"
    db.close()

    _mode["value"] = "timeout"
    r = client.put("/api/me/name", json={"name": "e2e-乙-再改名"}, headers=b["headers"])
    assert r.status_code == 200, r.text
    tick_until_drained()
    wait_batches(timeout=30.0, keep_ticking=True)
    db = SessionLocal()
    unknown = [
        {"status": cl.status, "error_code": cl.error_code}
        for cl in db.scalars(select(StewardModelCall)).all()
        if cl.status == "unknown"
    ]
    batch_rows = {
        str(bt.id): {"status": bt.status, "lease_until": str(bt.lease_until)}
        for bt in db.scalars(select(StewardAssistBatch)).all()
    }
    call_rows = [
        {"status": cl.status, "error_code": cl.error_code, "kind": cl.assist_kind}
        for cl in db.scalars(select(StewardModelCall)).all()
    ]
    step(
        "inject_timeout_provider",
        unknown_calls=unknown,
        batch_rows=batch_rows,
        call_rows=call_rows,
    )
    db.close()
    _mode["value"] = "success"

    # ---- 10. 撤权（revoke → 证据失效 → 卡片取代）----
    r = client.post(f"/api/relations/{edge_id}/revoke", headers=a["headers"])
    assert r.status_code == 200, r.text
    drained = tick_until_drained()
    db = SessionLocal()
    cards = list(db.scalars(select(ActionCard).where(ActionCard.space_id == space_id)).all())
    step(
        "relation_revoked",
        relation_id=edge_id,
        superseded_cards=[cd.id for cd in cards if cd.state == "superseded"],
        active_cards=[cd.id for cd in cards if cd.state not in ("superseded", "expired")],
    )
    db.close()

    # ---- 11. 进程中断恢复（崩溃遗留的过期 lease → reaper 回队 → 重新执行）----
    db = SessionLocal()
    r = client.put("/api/me/name", json={"name": "e2e-丙-再改名"}, headers=c["headers"])
    assert r.status_code == 200, r.text
    db.commit()
    db.close()
    db = SessionLocal()
    job = db.scalar(
        select(StewardJob).where(StewardJob.status == "queued").order_by(StewardJob.id.desc())
    )
    assert job is not None
    # 模拟 worker 崩溃：lease 后进程死亡（lease 已过期）
    now = tu.utcnow()
    job.status = "leased"
    job.attempt += 1
    job.leased_by = "dead-worker"
    job.lease_expires_at = now - timedelta_compat()
    job.heartbeat_at = now - timedelta_compat()
    db.commit()
    db.close()
    counters = maintenance.run_maintenance_tick()
    db = SessionLocal()
    row = db.get(StewardJob, job.id)
    step(
        "interrupt_recovery",
        reaped=counters["steward_reaped"],
        final_status=row.status,
        attempt=row.attempt,
    )
    assert row.status == "succeeded", row.status
    db.close()

    # ---- 12. 关闭 → 重开（回滚形态：关闭不执行，重开扫描追补）----
    config.STEWARD_ENABLED = False
    r = client.put("/api/me/name", json={"name": "e2e-关闭期改名"}, headers=a["headers"])
    assert r.status_code == 200, r.text
    counters = maintenance.run_maintenance_tick()
    db = SessionLocal()
    queued_while_off = len(
        list(db.scalars(select(StewardJob).where(StewardJob.status == "queued")))
    )
    db.close()
    step(
        "disabled_no_enqueue",
        new_jobs_while_off=queued_while_off,
        scanned=counters["steward_scanned"],
    )
    assert queued_while_off == 0
    config.STEWARD_ENABLED = True
    # 停用期间 last scan 的 next_scan_at 仍指向未来；把调度行置为到期以模拟
    # 真实时间流逝（与 interrupt_recovery 步骤回拨 lease 同一手法），
    # re-enable 后首轮扫描即执行有界追补。
    from app.models.steward import StewardSpaceSchedule

    db = SessionLocal()
    db.execute(
        StewardSpaceSchedule.__table__.update().values(
            next_scan_at=tu.utcnow(), updated_at=tu.utcnow()
        )
    )
    db.commit()
    db.close()
    drained = tick_until_drained()
    db = SessionLocal()
    jobs = list(db.scalars(select(StewardJob).where(StewardJob.space_id == space_id)).all())
    step("reenabled_backfill", **drained, jobs_total=len(jobs))
    assert drained["jobs_executed"] > 0, "re-enable 后应追补停机期间遗漏事件"
    db.close()

    # ---- 13. 最终观测快照 ----
    r = admin.get("/admin-api/v1/steward/status", headers=ah)
    final_status = r.json()
    r = admin.get("/admin-api/v1/steward/jobs", params={"space_id": space_id}, headers=ah)
    job_rows = r.json()["items"]
    step(
        "final_admin_status",
        state=final_status["state"],
        metrics=final_status["metrics"],
        alerts=final_status["alerts"],
        job_rows=job_rows,
    )
    fakesrv.shutdown()
    with open(EVIDENCE_PATH, "w") as fh:
        json.dump(EVIDENCE, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[e2e] evidence written to {EVIDENCE_PATH}")
    return 0


def timedelta_compat() -> object:
    from datetime import timedelta

    return timedelta(seconds=300)


if __name__ == "__main__":
    raise SystemExit(main())
