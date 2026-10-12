/**
 * Cross-language tool contract parity.
 *
 * The registry lives in two places that cannot import each other:
 * `backend/app/services/agent_tools.py` (Python, the side that validates and
 * executes) and `src/tools.ts` (this side, the declarations the model sees).
 * They were maintained by hand and drifted — `familygraph.propose_memory`
 * existed only on the backend, so every assistant run failed
 * `buildRunSession`'s allowlist check whenever platform memory was enabled.
 *
 * The snapshot `shared/agent-tool-contract.json` is generated from the Python
 * registry (`scripts/export-agent-tool-contract.py`) and is the single source of
 * truth for names, versions, `required_kind` and structural schema. This file is
 * one of the two gates; the other is
 * `backend/tests/test_agent_tool_registry_contract.py`, which keeps the snapshot
 * fresh. Both are needed: with only this one, a stale snapshot still matches the
 * declarations; with only the other, snapshot and declarations can drift together.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { adapterFor } from "../src/adapters/kind.js";
import { createDomainTools, TOOL_VERSIONS, type DomainToolExecutor } from "../src/tools.js";

interface ContractEntry {
  version: number;
  supported_versions: number[];
  required_kind: string | null;
  schema: Record<string, unknown>;
}

const snapshot = JSON.parse(
  readFileSync(new URL("../../shared/agent-tool-contract.json", import.meta.url), "utf8"),
) as { schema_version: number; tools: Record<string, ContractEntry> };

const contract = snapshot.tools;

const stubExecutor: DomainToolExecutor = async () => ({ ok: true, result: {} });

/**
 * Normalize a declaration for comparison. Mirrors
 * `agent_tools._schema_contract_view` step for step; the two must stay in sync
 * (a mismatch here shows up as a diff on every tool, which is the intended
 * failure mode — do not "fix" it by relaxing either side).
 *
 *  - `description` is dropped: it is model-facing prose, owned by this side, and
 *    can never match the backend's audit-facing text;
 *  - `required` defaults to `[]` because TypeBox omits it when nothing is required;
 *  - `properties` is sorted because literal order is not a contract;
 *  - boundary keywords are kept: they are the real admission limits and the two
 *    sides did not agree on them before this task;
 *  - `additionalProperties` is dropped **on purpose** — see the note below.
 *
 * Why `additionalProperties` is not part of the compared contract: the two sides
 * mean different things by it. On the backend it only *rejects* extra fields. On
 * this side the SDK's `validateToolArguments` uses it to *strip* them before the
 * tool runs, and the policy guard's scope-override hard block reads the
 * post-validation arguments — so declaring it here makes a real privilege
 * attempt invisible (no `POLICY_TOOL_BLOCKED`, no audit row, run succeeds).
 * The asymmetry is asserted explicitly below instead of being aligned away.
 */
function contractView(schema: unknown): unknown {
  if (schema === null || typeof schema !== "object" || Array.isArray(schema)) return schema;
  const source = schema as Record<string, unknown>;
  const view: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key === "description" || key === "additionalProperties") continue;
    view[key] = value;
  }
  if (view["properties"] && typeof view["properties"] === "object") {
    const properties = view["properties"] as Record<string, unknown>;
    view["properties"] = Object.fromEntries(
      Object.keys(properties)
        .sort()
        .map((key) => [key, contractView(properties[key])]),
    );
  }
  if (view["type"] === "object") view["required"] ??= [];
  if ("items" in view) view["items"] = contractView(view["items"]);
  return view;
}

/** Declarations as built at runtime, keyed by canonical name. */
function declaredSchemas(): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const tool of createDomainTools(stubExecutor)) {
    out[tool.name] = JSON.parse(JSON.stringify(tool.parameters)) as Record<string, unknown>;
  }
  return out;
}

describe("cross-language tool contract", () => {
  it("declares exactly the tools the backend registry defines", () => {
    // A tool present only in the backend is advertised to the model by the
    // server's allowlist and then rejected by buildRunSession — the failure this
    // task exists to fix. A tool present only here is a capability the server
    // will refuse at execute() time.
    expect(Object.keys(TOOL_VERSIONS).sort()).toEqual(Object.keys(contract).sort());
    expect(Object.keys(declaredSchemas()).sort()).toEqual(Object.keys(contract).sort());
  });

  it("sends a version the backend still accepts", () => {
    // Not equality: the sidecar may legitimately lag the backend's current
    // version. `get_relationship_path` / `explain_structural_path` are @2 with
    // @1 kept accepted, and the sidecar still calls @1 because switching is a
    // separate release decision. What must hold is that the version we send is
    // in the accepted set — otherwise every call fails AGENT_TOOL_VERSION_UNSUPPORTED.
    for (const [name, entry] of Object.entries(contract)) {
      const sent = TOOL_VERSIONS[name as keyof typeof TOOL_VERSIONS];
      expect(entry.supported_versions, name).toContain(sent);
      expect(sent, name).toBeLessThanOrEqual(entry.version);
    }
  });

  it("agrees on every tool's structural schema", () => {
    const declared = declaredSchemas();
    for (const [name, entry] of Object.entries(contract)) {
      expect(contractView(declared[name]), name).toEqual(entry.schema);
    }
  });

  it("keeps the additionalProperties asymmetry that the policy guard depends on", () => {
    // Backend rejects extra fields; this side must NOT declare the keyword for
    // assistant tools, because the SDK would then strip them before the policy
    // guard inspects the arguments — turning a hard block into silence.
    // Regression: test/worker.integration.test.ts
    // "reports the detected class, not a secret leak, when a block aborts the run".
    const declared = declaredSchemas();
    for (const [name, entry] of Object.entries(contract)) {
      if (entry.required_kind === "steward") continue;
      expect(declared[name]!["additionalProperties"], name).toBeUndefined();
    }
    // The steward declarations predate this task and keep the keyword; changing
    // that is a separate decision, so pin the current state rather than assume.
    for (const [name, entry] of Object.entries(contract)) {
      if (entry.required_kind !== "steward") continue;
      expect(declared[name]!["additionalProperties"], name).toBe(false);
    }
  });

  it("partitions the contract exactly between the two kinds", () => {
    const assistant = adapterFor("assistant").toolNames();
    const steward = adapterFor("steward").toolNames();
    const all = [...assistant, ...steward].sort();
    expect(all).toEqual(Object.keys(contract).sort());
    // Disjoint: a tool must not be registerable by both kinds.
    expect(assistant.filter((name) => steward.includes(name))).toEqual([]);
    // And each name must land on the kind the backend registered it for.
    for (const [name, entry] of Object.entries(contract)) {
      const owner = entry.required_kind === "steward" ? steward : assistant;
      expect(owner, name).toContain(name);
    }
  });

  it("covers the memory tools that previously existed only on the backend", () => {
    // Regression pin for the concrete defect: platform memory enabled puts these
    // two in every assistant allowlist, so both must be declared here or every
    // run fails before its first model request.
    for (const name of ["familygraph.search_memory", "familygraph.propose_memory"]) {
      expect(Object.keys(TOOL_VERSIONS), name).toContain(name);
      expect(adapterFor("assistant").toolNames(), name).toContain(name);
    }
  });
});
