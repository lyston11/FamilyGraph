/**
 * The Pi session id is the provider's `prompt_cache_key`.
 *
 * The SDK forwards `sessionManager.getSessionId()` to the provider adapter,
 * which sends it as `prompt_cache_key` for the `openai-responses` protocol this
 * deployment uses. A random per-run id therefore changed the cache key on every
 * run of one conversation, so a prefix that had not changed could never be
 * reused. These tests pin the key to the FamilyGraph session instead.
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  InternalClient,
  type RunContextMessage,
  type RunContextProjection,
} from "../src/client.js";
import { buildRunSession, type SessionBundle } from "../src/session.js";
import { makeAgentConfig } from "./helpers.js";

function projection(
  overrides: Partial<RunContextProjection> = {},
): RunContextProjection {
  const messages: RunContextMessage[] = [
    {
      id: 1,
      role: "user",
      content_json: { text: "hello" },
      created_at: new Date(Date.UTC(2026, 8, 1)).toISOString(),
    },
  ];
  const runId = overrides.run_id ?? "42";
  return {
    run_id: runId,
    session_id: "7",
    agent_kind: "assistant",
    account_id: "9",
    space_id: "8",
    status: "leased",
    attempt: 1,
    policy_version: "pv-test",
    tool_allowlist: ["familygraph.echo"],
    messages,
    next_event_seq: 1,
    context_build_id: null,
    provider: {
      provider_id: "3",
      provider_name: "cache-key-test-provider",
      model: "cache-key-test-model",
      kind: "local",
      api: "openai-completions",
      context_window: 272_000,
      max_tokens: 4_096,
      reasoning: false,
      input_modalities: ["text"],
      thinking_levels: [],
      policy_result: "allowed",
      secret_ref: null,
      // The provider projection is bound to its run; a mismatched id is
      // rejected before the session is built.
      base_url: `/internal/agent/runs/${runId}/provider`,
      api_key: null,
    },
    cancel_requested: false,
    ...overrides,
  };
}

describe("provider prompt cache key", () => {
  const sessions: SessionBundle["session"][] = [];
  const dirs: string[] = [];

  afterEach(() => {
    for (const session of sessions.splice(0)) session.dispose();
    for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
  });

  async function buildSessionId(context: RunContextProjection): Promise<string> {
    const config = makeAgentConfig(1);
    const client = new InternalClient(config);
    vi.spyOn(client, "executeTool");
    const agentDir = mkdtempSync(join(tmpdir(), "fg-cache-key-test-"));
    dirs.push(agentDir);
    const bundle = await buildRunSession(config, client, context, "synthetic-run-token", {
      agentDir,
      // No provider traffic: this test is about the session identity the SDK
      // forwards, not about the wire.
      streamOverride: () => {
        throw new Error("provider must not be contacted in this test");
      },
    });
    sessions.push(bundle.session);
    return bundle.session.sessionId;
  }

  it("is stable across runs of one FamilyGraph session", async () => {
    // Two runs of the same conversation differ only by run id and attempt.
    const first = await buildSessionId(projection({ run_id: "42", attempt: 1 }));
    const second = await buildSessionId(projection({ run_id: "43", attempt: 1 }));
    const retried = await buildSessionId(projection({ run_id: "42", attempt: 2 }));

    expect(second).toBe(first);
    expect(retried).toBe(first);
    // A fresh uuid per run is exactly the regression this pins: the key must
    // not be a random id, or the upstream cache can never be reused.
    expect(first).toMatch(/^fg-/);
  });

  it("separates different FamilyGraph sessions", async () => {
    const base = await buildSessionId(projection());
    const otherSession = await buildSessionId(projection({ session_id: "77" }));

    expect(otherSession).not.toBe(base);
  });

  it("separates accounts sharing one provider deployment", async () => {
    // The upstream cache is scoped to the provider account. Two FamilyGraph
    // accounts on one deployment must not partition each other's cache.
    const base = await buildSessionId(projection());
    const otherAccount = await buildSessionId(projection({ account_id: "99" }));

    expect(otherAccount).not.toBe(base);
  });
});
