/**
 * The kind adapter is the one place the two runtime kinds differ, so each
 * difference gets an assertion here.
 *
 * Why per-member rather than one "adapters work" test: the value of this
 * refactor is that a kind difference is *enumerable*. A single smoke test would
 * pass with a member silently returning the wrong kind's value (both adapters
 * are structurally identical), which is exactly the failure the old inline
 * `if (kind === "steward")` branches had. Each case below therefore pins one
 * member to a value that is wrong for the other kind.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { adapterFor } from "../src/adapters/kind.js";
import type { AgentConfig } from "../src/config.js";
import { makeAgentConfig } from "./helpers.js";

const config: AgentConfig = {
  ...makeAgentConfig(0),
  maxConcurrentRuns: 3,
  stewardMaxConcurrentCallsPerSpace: 5,
};

function projection(overrides: Record<string, unknown> = {}) {
  return {
    run_id: "7",
    session_id: "11",
    agent_kind: "assistant",
    account_id: "22",
    space_id: "33",
    status: "running",
    attempt: 1,
    policy_version: "pv",
    tool_allowlist: [],
    messages: [],
    next_event_seq: 1,
    context_build_id: null,
    provider: null,
    cancel_requested: false,
    ...overrides,
  } as never;
}

describe("adapterFor", () => {
  it("is total over the kind union and returns frozen singletons", () => {
    expect(adapterFor("assistant").kind).toBe("assistant");
    expect(adapterFor("steward").kind).toBe("steward");
    // Frozen: a mutation from one run must not reach the next.
    expect(Object.isFrozen(adapterFor("assistant"))).toBe(true);
    expect(Object.isFrozen(adapterFor("steward"))).toBe(true);
  });

  it("gives each kind its own system prompt", () => {
    const assistant = adapterFor("assistant").systemPrompt;
    const steward = adapterFor("steward").systemPrompt;
    // Sharing one prompt is the specific failure: the assistant prompt asks for
    // prose and tool calls, the steward's output is a closed structured product
    // the server validates.
    expect(assistant).not.toBe(steward);
    expect(assistant.length).toBeGreaterThan(0);
    expect(steward.length).toBeGreaterThan(0);
  });

  it("only rejects an empty tool allowlist for the assistant", () => {
    expect(adapterFor("assistant").emptyToolAllowlistIsInvalid).toBe(true);
    expect(adapterFor("steward").emptyToolAllowlistIsInvalid).toBe(false);
  });

  it("registers no tools for the steward", () => {
    expect(adapterFor("steward").toolNames()).toEqual([]);
    expect(adapterFor("assistant").toolNames().length).toBeGreaterThan(0);
  });

  it("derives a different cache key per kind", () => {
    const assistantKey = adapterFor("assistant").cacheKey(
      projection({ account_id: "22", session_id: "11", space_id: "33" }),
    );
    const stewardKey = adapterFor("steward").cacheKey(
      projection({ account_id: null, session_id: null, space_id: "33" }),
    );
    expect(assistantKey).toBe("fg-22-11");
    expect(stewardKey).toBe("fg-steward-33");
    // The assistant formula against a steward projection is the bug the
    // per-kind key exists to prevent: every space would share one prefix.
    expect(assistantKey).not.toBe(stewardKey);
  });

  it("uses the kind's own slot budget", () => {
    expect(adapterFor("assistant").slotBudget(config)).toBe(3);
    expect(adapterFor("steward").slotBudget(config)).toBe(5);
  });

  it("takes the server broadcast only for the steward", () => {
    expect(adapterFor("assistant").adoptsServerConcurrency).toBe(false);
    expect(adapterFor("steward").adoptsServerConcurrency).toBe(true);
  });

  it("reports a product on settle only for the steward", () => {
    // The steward's product cannot travel through message events (child runs
    // refuse them), so settlement is its only route; the assistant's text is
    // published as a message event instead.
    expect(adapterFor("assistant").reportsProductOnSettle).toBe(false);
    expect(adapterFor("steward").reportsProductOnSettle).toBe(true);
  });

  it("extracts the final text as the product for both kinds", () => {
    expect(adapterFor("assistant").extractProduct("hello")).toBe("hello");
    expect(adapterFor("steward").extractProduct('{"items":[]}')).toBe('{"items":[]}');
    expect(adapterFor("assistant").extractProduct(null)).toBeNull();
    expect(adapterFor("steward").extractProduct(null)).toBeNull();
  });
});

describe("leaseRequest", () => {
  it("routes each kind to its own endpoint with no space_id", () => {
    const assistant = adapterFor("assistant").leaseRequest(config);
    const steward = adapterFor("steward").leaseRequest(config);

    expect(assistant.path).toBe("/internal/agent/jobs/lease");
    expect(assistant.body).toEqual({ kind: "assistant", leased_by: config.sidecarId });
    // Named for what is leased (an attempt), and a separate route: "which
    // container may lease which queue" is a routing constraint.
    expect(steward.path).toBe("/internal/agent/steward/attempts/lease");
    expect(steward.body).toEqual({ kind: "steward", leased_by: config.sidecarId });
    // The sidecar has no view of the space topology; the server picks the space.
    expect(steward.body).not.toHaveProperty("space_id");
  });
});

describe("decodeLease", () => {
  it("accepts a well-formed steward lease", () => {
    const decoded = adapterFor("steward").decodeLease({
      steward_job_id: 4,
      assist_attempt_id: 9,
      assist_kind: "terminology",
      max_concurrent: 2,
    });
    expect(decoded).toEqual({
      steward_job_id: "4",
      steward_attempt_id: "9",
      assist_kind: "terminology",
      max_concurrent: 2,
    });
  });

  it("treats a missing attempt id as null rather than failing", () => {
    const decoded = adapterFor("steward").decodeLease({
      steward_job_id: 4,
      max_concurrent: 2,
    });
    expect(decoded.steward_attempt_id).toBeNull();
  });

  it("fails closed on a malformed steward lease", () => {
    // Each field is load-bearing: a lease without a job id has no authorization
    // root, and one without a concurrency broadcast cannot size its budget.
    expect(() => adapterFor("steward").decodeLease({ max_concurrent: 2 })).toThrow();
    expect(() => adapterFor("steward").decodeLease({ steward_job_id: 4 })).toThrow();
    expect(() =>
      adapterFor("steward").decodeLease({ steward_job_id: 4, max_concurrent: 0 }),
    ).toThrow();
  });

  it("contributes nothing for the assistant lease", () => {
    expect(adapterFor("assistant").decodeLease({ job_id: 1 })).toEqual({});
  });
});

describe("kind differences are confined to the adapter", () => {
  it("has no agent-kind branch left in the lifecycle or protocol code", () => {
    // The property this refactor buys: a kind difference is a member added here,
    // not an `if` threaded through the run lifecycle. Asserted structurally
    // because "we removed the branches" is otherwise a claim about a moment in
    // time — the next kind difference can always be added back inline, and that
    // is exactly how the two concerns drifted together before.
    //
    // `provider.kind` is a different union (openai_compatible|local) and is not
    // an agent-kind branch; the registry in tools.ts and the role switch in
    // config.ts are the kind→behaviour tables the adapter reads from.
    const files = ["src/worker.ts", "src/session.ts", "src/client.ts", "src/events.ts"];
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
      source.split("\n").forEach((line, index) => {
        // Comments explain the old branches; only code counts.
        const code = line.replace(/\/\/.*$/, "").replace(/^\s*\*.*$/, "");
        if (/(agent_kind|kind)\s*[!=]==\s*"(assistant|steward)"/.test(code)) {
          offenders.push(`${file}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
