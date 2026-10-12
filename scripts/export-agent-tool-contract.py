#!/usr/bin/env python3
"""Export the agent tool contract from the backend registry into `shared/`.

Why this exists
---------------
The tool registry lives in two places that cannot import each other:
`backend/app/services/agent_tools.py` (Python, the enforcement side) and
`agent/src/tools.ts` (TypeScript, the side the model sees). They were maintained
by hand and drifted — `familygraph.propose_memory` existed only on the backend
side, so every assistant run failed the sidecar's allowlist check whenever
platform memory was enabled.

The direction is one-way: the Python `REGISTRY` is authoritative because it is
what actually validates and executes. This script serializes it to
`shared/agent-tool-contract.json`, and two gates compare against that file:

  - `backend/tests/test_agent_tool_registry_contract.py` — the snapshot is fresh
    (regenerating it must be a no-op);
  - `agent/test/tool-contract-parity.test.ts` — the sidecar declarations match it.

Run without arguments to (re)write the snapshot; `--check` to verify freshness
without writing (used by the backend test).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
SNAPSHOT = REPO_ROOT / "shared" / "agent-tool-contract.json"

sys.path.insert(0, str(BACKEND))


def render() -> str:
    """Canonical snapshot text. Must be byte-identical across runs and platforms."""
    from app.services import agent_tools

    payload = {
        "schema_version": agent_tools.CONTRACT_SCHEMA_VERSION,
        "source": "backend/app/services/agent_tools.py::REGISTRY",
        "tools": agent_tools.canonical_contract(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the snapshot is fresh; exit 1 when it differs (no write)",
    )
    args = parser.parse_args()

    rendered = render()
    if args.check:
        if not SNAPSHOT.exists():
            print(f"missing snapshot: {SNAPSHOT}", file=sys.stderr)
            return 1
        if SNAPSHOT.read_text(encoding="utf-8") != rendered:
            print(
                f"stale snapshot: {SNAPSHOT}\n"
                f"run: python3 scripts/export-agent-tool-contract.py",
                file=sys.stderr,
            )
            return 1
        print(f"snapshot is fresh: {SNAPSHOT}")
        return 0

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(rendered, encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
