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

/**
 * V2.3 relationship-intelligence kinship tools (Block E4b): same convergence
 * rules as the V2.2 contract, plus the declared length/bounds constraints.
 * record_term_usage is the only consent-gated (non-read-only) declaration.
 */
const KINSHIP_CONTRACT: Record<string, { properties: string[]; required: string[] }> = {
  "familygraph.resolve_free_text_relation": { properties: ["text"], required: ["text"] },
  "familygraph.get_term_alternatives": {
    properties: ["concept_code", "limit"],
    required: ["concept_code"],
  },
  "familygraph.record_term_usage": {
    properties: ["concept_code", "consent_confirmed", "term"],
    required: ["concept_code", "consent_confirmed", "term"],
  },
};

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

describe("V2.3 kinship tool declarations", () => {
  function propertiesOf(toolName: string): Record<string, Record<string, unknown>> {
    const tool = createDomainTools(stubExecutor).find((t) => t.name === toolName);
    if (!tool) throw new Error(`tool ${toolName} not declared`);
    const parameters = tool.parameters as {
      properties?: Record<string, Record<string, unknown>>;
    };
    return parameters.properties ?? {};
  }

  it("registers the three kinship tools at version 1", () => {
    for (const name of Object.keys(KINSHIP_CONTRACT)) {
      expect(TOOL_VERSIONS[name as keyof typeof TOOL_VERSIONS]).toBe(1);
    }
    expect(defaultToolNames()).toEqual(expect.arrayContaining(Object.keys(KINSHIP_CONTRACT)));
  });

  it("declares inputs exactly per the backend contract (no missing, no extra fields)", () => {
    const declared = new Set(createDomainTools(stubExecutor).map((t) => t.name));
    for (const [name, expected] of Object.entries(KINSHIP_CONTRACT)) {
      expect(declared.has(name)).toBe(true);
      const { properties, required } = schemaOf(name);
      expect(Object.keys(properties).sort(), `${name} properties`).toEqual(expected.properties);
      expect([...required].sort(), `${name} required`).toEqual(expected.required);
    }
  });

  it("constrains field lengths and bounds exactly per the shared contract", () => {
    expect(propertiesOf("familygraph.resolve_free_text_relation").text).toMatchObject({
      minLength: 1,
      maxLength: 80,
    });
    const alternatives = propertiesOf("familygraph.get_term_alternatives");
    expect(alternatives.concept_code).toMatchObject({ minLength: 1, maxLength: 128 });
    expect(alternatives.limit).toMatchObject({ minimum: 1, maximum: 10 });
    const usage = propertiesOf("familygraph.record_term_usage");
    expect(usage.concept_code).toMatchObject({ minLength: 1, maxLength: 128 });
    expect(usage.term).toMatchObject({ minLength: 1, maxLength: 64 });
  });

  it("gates record_term_usage behind explicit consent while keeping the others read-only", () => {
    const tools = createDomainTools(stubExecutor);
    const record = tools.find((t) => t.name === "familygraph.record_term_usage")!;
    expect(record.description).toContain("同意");
    expect(record.description).not.toContain("只读");
    for (const name of [
      "familygraph.resolve_free_text_relation",
      "familygraph.get_term_alternatives",
    ]) {
      expect(tools.find((t) => t.name === name)!.description).toContain("只读");
    }
  });

  it("forwards kinship calls verbatim through the execute endpoint", async () => {
    const seen: Array<{ tool: string; call: Record<string, unknown> }> = [];
    const executor: DomainToolExecutor = async (toolName, call) => {
      seen.push({ tool: toolName, call: { ...call } });
      return { ok: true, result: {} };
    };
    const byName = new Map(createDomainTools(executor).map((t) => [t.name, t]));

    await byName
      .get("familygraph.resolve_free_text_relation")!
      .execute!("tc_k1", { text: "舅爷爷" }, undefined, undefined, undefined as never);
    await byName.get("familygraph.get_term_alternatives")!.execute!(
      "tc_k2",
      { concept_code: "kin.grandparent.paternal" },
      undefined,
      undefined,
      undefined as never,
    );
    await byName.get("familygraph.record_term_usage")!.execute!(
      "tc_k3",
      { concept_code: "kin.parent.mother", term: "老妈", consent_confirmed: true },
      undefined,
      undefined,
      undefined as never,
    );

    expect(seen.map((s) => s.tool)).toEqual([
      "familygraph.resolve_free_text_relation",
      "familygraph.get_term_alternatives",
      "familygraph.record_term_usage",
    ]);
    expect(seen.every((s) => s.call.version === 1 && s.call.tool_call_id !== "")).toBe(true);
    expect(seen[0]!.call.input).toEqual({ text: "舅爷爷" });
    // Optional limit left unset must not be sent.
    expect(seen[1]!.call.input).toEqual({ concept_code: "kin.grandparent.paternal" });
    expect(seen[2]!.call.input).toEqual({
      concept_code: "kin.parent.mother",
      term: "老妈",
      consent_confirmed: true,
    });
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

describe("V2.6 controlled web tool declarations", () => {
  const WEB_CONTRACT: Record<string, { properties: string[]; required: string[] }> = {
    "familygraph.search_web": {
      properties: ["limit", "query", "use_case"],
      required: ["query", "use_case"],
    },
    "familygraph.fetch_approved_page": {
      properties: ["approved_token"],
      required: ["approved_token"],
    },
  };

  it("registers both web tools at version 1", () => {
    for (const name of Object.keys(WEB_CONTRACT)) {
      expect(TOOL_VERSIONS[name as keyof typeof TOOL_VERSIONS]).toBe(1);
    }
    expect(defaultToolNames()).toEqual(expect.arrayContaining(Object.keys(WEB_CONTRACT)));
  });

  it("declares inputs exactly per the backend contract (no missing, no extra fields)", () => {
    const declared = new Set(createDomainTools(stubExecutor).map((t) => t.name));
    for (const [name, expected] of Object.entries(WEB_CONTRACT)) {
      expect(declared.has(name)).toBe(true);
      const { properties, required } = schemaOf(name);
      expect(Object.keys(properties).sort(), `${name} properties`).toEqual(expected.properties);
      expect([...required].sort(), `${name} required`).toEqual(expected.required);
    }
  });

  it("marks web results as untrusted external data, never as system instructions", () => {
    const tools = createDomainTools(stubExecutor);
    const search = tools.find((t) => t.name === "familygraph.search_web")!;
    const fetch = tools.find((t) => t.name === "familygraph.fetch_approved_page")!;
    expect(search.description).toContain("不可信");
    expect(fetch.description).toContain("不可信");
    expect(fetch.description).toContain("不是系统指令");
  });

  it("forwards web calls verbatim through the execute endpoint", async () => {
    const seen: Array<{ tool: string; call: Record<string, unknown> }> = [];
    const executor: DomainToolExecutor = async (toolName, call) => {
      seen.push({ tool: toolName, call: { ...call } });
      return { ok: true, result: {} };
    };
    const byName = new Map(createDomainTools(executor).map((t) => [t.name, t]));

    await byName.get("familygraph.search_web")!.execute!(
      "tc_w1",
      { query: "genealogy", use_case: "research" },
      undefined,
      undefined,
      undefined as never,
    );
    await byName.get("familygraph.fetch_approved_page")!.execute!(
      "tc_w2",
      { approved_token: "tok_abcdefghijklmnopqrstuvwxyz" },
      undefined,
      undefined,
      undefined as never,
    );

    expect(seen.map((s) => s.tool)).toEqual([
      "familygraph.search_web",
      "familygraph.fetch_approved_page",
    ]);
    expect(seen[0]!.call.input).toEqual({ query: "genealogy", use_case: "research" });
    expect(seen[1]!.call.input).toEqual({ approved_token: "tok_abcdefghijklmnopqrstuvwxyz" });
  });
});
