"""Offline diagnostic against real Memory confirmation and ContextBuilder.

Run from the target backend with PYTHONPATH=.:tests and pytest -p conftest.
FG_RAG_PROBE_REPORT is a synthetic-only JSON output path. The 16 core cases
are a regression gate; independent extensions remain diagnostic, not tuning
targets or model-answer-quality measurements.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
from pathlib import Path

from conftest import create_agent_fixture, create_agent_message, create_agent_session

from app import config
from app.models.agent_provider import AgentProvider, AgentSpaceProviderSetting
from app.services import agent_queue, context_builder, memory_rag
from app.utils.timeutil import utcnow


def test_frozen_retrieval_fixture(db_session, monkeypatch):
    monkeypatch.setattr(config, "MEMORY_ENABLED", True)
    monkeypatch.setattr(config, "RAG_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_RUNTIME_ENABLED", True)
    fixture_path = Path(__file__).with_name("retrieval-fixture-v1.json")
    fixture = json.loads(fixture_path.read_text())
    actor, space = create_agent_fixture(db_session, name="synthetic-rag-probe")
    now = utcnow()
    provider = AgentProvider(
        name="synthetic-probe-local",
        kind="local",
        api="openai-completions",
        base_url="http://127.0.0.1:9/v1",
        allowed_models_json=["probe-model"],
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(provider)
    db_session.flush()
    db_session.add(AgentSpaceProviderSetting(
        space_id=space.id, agent_kind="assistant", provider_id=provider.id,
        model="probe-model", enabled=True, cloud_allowed=False, local_required=False,
    ))
    db_session.commit()
    labels = {}
    for label, material in fixture["memories"].items():
        candidate = memory_rag.propose_candidate(
            db_session, author_account_id=actor.account.id,
            source={"kind": "manual"}, source_quote=material["text"],
            summary=material["text"], suggested_scope=material["scope"],
            purpose="synthetic retrieval evaluation",
        )
        db_session.commit()
        memory = memory_rag.confirm_candidate(
            db_session, candidate_id=candidate.id,
            confirmer=actor, confirmer_account=actor.account,
            scope="private" if material["scope"] == "private" else f"household:{space.id}",
        )
        db_session.commit()
        labels[str(memory.id)] = label

    results = []
    for case in fixture["cases"]:
        session = create_agent_session(
            db_session, account_id=actor.account.id, space_id=space.id,
        )
        for previous in case["history"]:
            create_agent_message(db_session, session, content={"text": previous})
        current = create_agent_message(db_session, session, content={"text": case["query"]})
        run = agent_queue.enqueue_run(
            db_session, agent_session=session, message=current,
            kind="assistant", policy_version=config.POLICY_VERSION, tool_allowlist=[],
        )
        grant = agent_queue.lease_next(
            db_session, kind="assistant", leased_by="synthetic-retrieval-probe",
        )
        assert grant is not None and grant.run.id == run.id
        built = context_builder.ContextBuilder(db_session).build(
            actor=actor, space_id=space.id, agent_kind="assistant", query=case["query"],
            run_id=run.id, attempt=run.attempt, provider_kind="local", token_budget=2000,
        )
        db_session.commit()
        chunks = [labels.get(source.source_id, "unknown") for source in built.sources]
        ordered = list(dict.fromkeys(chunks))[:5]
        found = [target for target in case["targets"] if target in ordered]
        complete_fact = all(
            any(
                labels.get(source.source_id) == target
                and fixture["memories"][target]["required_chunk_fact"] in source.text
                for source in built.sources
            )
            for target in found if "required_chunk_fact" in fixture["memories"][target]
        )
        ranks = [ordered.index(target) + 1 for target in found]
        results.append({
            "id": case["id"], "group": case["group"], "targets": case["targets"],
            "source_labels_at_5": ordered, "chunk_source_labels": chunks,
            "target_source_ranks": ranks,
            "hit": bool(found) and complete_fact,
            "source_recall_at_5": len(found) / len(case["targets"]) if case["targets"] else None,
            "reciprocal_rank": 1 / min(ranks) if ranks else 0,
            "complete_cross_boundary_fact": complete_fact,
            "excluded_count": len(built.excluded),
        })
        agent_queue.settle_run(db_session, run, status="succeeded")

    core = [result for result in results if result["group"] == "core"]
    summaries = {}
    for group in ("core", "english", "extension"):
        selected = [result for result in results if result["group"] == group]
        summaries[group] = {
            "cases": len(selected), "hits": sum(result["hit"] for result in selected),
            "mean_recall_at_5": sum(result["source_recall_at_5"] for result in selected) / len(selected),
            "mrr": sum(result["reciprocal_rank"] for result in selected) / len(selected),
        }
    source_paths = [Path(inspect.getfile(module)) for module in (memory_rag, context_builder)]
    report = {
        "fixture_version": fixture["version"],
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "source_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
        "scope": "real confirmation + ContextBuilder + SQLite FTS; no model calls; synthetic data only",
        "summaries": summaries, "results": results,
    }
    output = os.environ.get("FG_RAG_PROBE_REPORT")
    if output:
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summaries, ensure_ascii=False))
    assert sum(result["hit"] for result in core) == 16
