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
 *    sides did not agree on them before this task.
 */
function contractView(schema: unknown): unknown {
  if (schema === null || typeof schema !== "object" || Array.isArray(schema)) return schema;
  const source = schema as Record<string, unknown>;
  const view: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (key === "description") continue;
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

  it("agrees on every tool version", () => {
    for (const [name, entry] of Object.entries(contract)) {
      expect(TOOL_VERSIONS[name as keyof typeof TOOL_VERSIONS], name).toBe(entry.version);
    }
  });

  it("agrees on every tool's structural schema", () => {
    const declared = declaredSchemas();
    for (const [name, entry] of Object.entries(contract)) {
      expect(contractView(declared[name]), name).toEqual(entry.schema);
    }
  });

  it("declares additionalProperties=false on every object schema", () => {
    // The schema-level expression of "the model may not send space_id /
    // account_id / arbitrary permission fields". Leaving it unstated is not
    // "allowed" and must not be read as such.
    for (const [name, entry] of Object.entries(contract)) {
      expect(entry.schema["additionalProperties"], name).toBe(false);
    }
    for (const [name, schema] of Object.entries(declaredSchemas())) {
      expect(schema["additionalProperties"], name).toBe(false);
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
