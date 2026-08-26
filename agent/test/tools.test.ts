/**
 * V2.2 shared-contract completeness tests for the domain tool registry.
 *
 * The backend (Block C1) registers the same six read-only assistant query
 * tools plus steward_ping; these declarations must match the contract exactly
 * — same names, version 1, identical input field composition, no extra fields.
 */

import { describe, expect, it } from "vitest";
import {
  createDomainTools,
  defaultToolNames,
  TOOL_VERSIONS,
  type DomainToolExecutor,
} from "../src/tools.js";

/** Contract: tool name → sorted property names + required fields. */
const SHARED_CONTRACT: Record<string, { properties: string[]; required: string[] }> = {
  "familygraph.get_self_context": { properties: [], required: [] },
  "familygraph.list_visible_people": {
    properties: ["cursor", "limit", "query"],
    required: [],
  },
  "familygraph.get_profile_summary": { properties: ["user_id"], required: ["user_id"] },
  "familygraph.search_space": { properties: ["limit", "query"], required: ["query"] },
  "familygraph.get_relationship_path": {
    properties: ["from_user_id", "to_user_id"],
    required: ["to_user_id"],
  },
  "familygraph.explain_structural_path": {
    properties: ["from_user_id", "to_user_id"],
    required: ["to_user_id"],
  },
  "familygraph.steward_ping": { properties: [], required: [] },
};

const V2_2_TOOL_NAMES = Object.keys(SHARED_CONTRACT);

const stubExecutor: DomainToolExecutor = async () => ({ ok: true, result: {} });

function schemaOf(toolName: string): {
  properties: Record<string, unknown>;
  required: string[];
} {
  const tools = createDomainTools(stubExecutor);
  const tool = tools.find((t) => t.name === toolName);
  if (!tool) throw new Error(`tool ${toolName} not declared`);
  const parameters = tool.parameters as {
    properties?: Record<string, unknown>;
    required?: string[];
  };
  return { properties: parameters.properties ?? {}, required: parameters.required ?? [] };
}

describe("V2.2 domain tool declarations", () => {
  it("registers the six assistant query tools and steward_ping at version 1", () => {
    for (const name of V2_2_TOOL_NAMES) {
      expect(TOOL_VERSIONS[name as keyof typeof TOOL_VERSIONS]).toBe(1);
    }
    expect(defaultToolNames()).toEqual(expect.arrayContaining(V2_2_TOOL_NAMES));
    // V2.1 skeleton diagnostics remain registered.
    expect(defaultToolNames()).toEqual(
      expect.arrayContaining(["familygraph.echo", "familygraph.probe_scope"]),
    );
  });

  it("declares inputs exactly per the shared contract (no missing, no extra fields)", () => {
    const tools = createDomainTools(stubExecutor);
    const declared = new Set(tools.map((t) => t.name));
    for (const [name, expected] of Object.entries(SHARED_CONTRACT)) {
      expect(declared.has(name)).toBe(true);
      const { properties, required } = schemaOf(name);
      expect(Object.keys(properties).sort(), `${name} properties`).toEqual(expected.properties);
      expect([...required].sort(), `${name} required`).toEqual(expected.required);
    }
  });

  it("documents every new tool in Chinese with explicit read-only semantics", () => {
    const tools = createDomainTools(stubExecutor);
    for (const name of V2_2_TOOL_NAMES) {
      const tool = tools.find((t) => t.name === name)!;
      expect(tool.description.length).toBeGreaterThan(10);
      expect(tool.description).toContain("只读");
    }
  });
});

describe("domain tool executor bridge", () => {
  it("forwards tool_call_id + version + exact input through the execute endpoint", async () => {
    const seen: Array<{ tool: string; call: Record<string, unknown> }> = [];
    const executor: DomainToolExecutor = async (toolName, call) => {
      seen.push({ tool: toolName, call: { ...call } });
      return { ok: true, result: { user_id: 7, name: "王明", fact_state: "confirmed" } };
    };
    const tools = createDomainTools(executor);
    const byName = new Map(tools.map((t) => [t.name, t]));

    const profileTool = byName.get("familygraph.get_profile_summary")!;
    const outcome = await profileTool.execute!(
      "tc_x",
      { user_id: 7 },
      undefined,
      undefined,
      undefined as never,
    );

    expect(seen).toEqual([
      {
        tool: "familygraph.get_profile_summary",
        call: { tool_call_id: "tc_x", version: 1, input: { user_id: 7 } },
      },
    ]);
    // Projected output is surfaced to the model as JSON text.
    const text = (outcome.content[0] as { type: string; text: string }).text;
    expect(JSON.parse(text)).toEqual({ user_id: 7, name: "王明", fact_state: "confirmed" });
  });

  it("omits unset optional list fields instead of sending them empty", async () => {
    const inputs: unknown[] = [];
    const executor: DomainToolExecutor = async (_toolName, call) => {
      inputs.push(call.input);
      return { ok: true, result: { people: [], next_cursor: null } };
    };
    const tools = createDomainTools(executor);
    const listTool = tools.find((t) => t.name === "familygraph.list_visible_people")!;
    await listTool.execute!("tc_y", { query: "王" }, undefined, undefined, undefined as never);

    expect(inputs).toEqual([{ query: "王" }]);
  });
});
