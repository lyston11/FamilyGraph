/**
 * A-03 measurement: the gap between upstream assistant text deltas and the
 * first *visible* assistant event, against the real Pi SDK, with no egress.
 *
 * The product contract is that `mapSessionEvent` publishes an assistant
 * message only at `message_end` (see spec/backend/agent-runtime.md §4). That
 * makes "first visible text" strictly later than "first upstream text delta".
 * This test measures that gap so the claim is executable evidence rather than
 * an assumption, and it fails if the delta is ever silently published.
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import {
  createAssistantMessageEventStream,
  type Api,
  type AssistantMessage,
  type Context,
  type Model,
} from "@earendil-works/pi-ai";
import { InternalClient, type RunContextProjection } from "../src/client.js";
import { mapSessionEvent } from "../src/events.js";
import { buildRunSession, type BuildSessionDeps, type SessionBundle } from "../src/session.js";
import { makeAgentConfig } from "./helpers.js";

const ANSWER_PARTS = ["The blue ", "tin is ", "in the attic."];
const DELTA_INTERVAL_MS = 40;

function projection(): RunContextProjection {
  return {
    run_id: "42",
    session_id: "7",
    agent_kind: "assistant",
    account_id: "9",
    space_id: "8",
    status: "leased",
    attempt: 1,
    policy_version: "pv-test",
    tool_allowlist: ["familygraph.echo"],
    messages: [
      {
        id: 1,
        role: "user",
        content_json: { text: "Where is the blue tin?" },
        created_at: new Date(Date.UTC(2026, 8, 1, 0, 0, 1)).toISOString(),
      },
    ],
    next_event_seq: 1,
    context_build_id: null,
    provider: {
      provider_id: "3",
      provider_name: "delta-gap-provider",
      model: "delta-gap-model",
      kind: "local",
      api: "openai-completions",
      context_window: 272_000,
      max_tokens: 4_096,
      reasoning: false,
      input_modalities: ["text"],
      thinking_levels: [],
      policy_result: "allowed",
      secret_ref: null,
      base_url: "/internal/agent/runs/42/provider",
      api_key: null,
    },
    cancel_requested: false,
  };
}

/** Streams the answer in several text deltas with real (short) delays. */
function deltaStream(deltaTimes: number[]): NonNullable<BuildSessionDeps["streamOverride"]> {
  return (model: Model<Api>, context: Context) => {
    const stream = createAssistantMessageEventStream();
    const full = ANSWER_PARTS.join("");
    const base: AssistantMessage = {
      role: "assistant",
      content: [{ type: "text", text: "" }],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: {
        input: 12,
        output: 8,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 20,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: "stop",
      timestamp: Date.now(),
    };
    stream.push({ type: "start", partial: base });

    let sent = "";
    void (async () => {
      for (const part of ANSWER_PARTS) {
        await new Promise((resolve) => setTimeout(resolve, DELTA_INTERVAL_MS));
        sent += part;
        deltaTimes.push(Date.now());
        stream.push({
          type: "text_delta",
          contentIndex: 0,
          delta: part,
          partial: { ...base, content: [{ type: "text", text: sent }] },
        });
      }
      const done: AssistantMessage = { ...base, content: [{ type: "text", text: full }] };
      stream.push({ type: "done", reason: "stop", message: done });
      stream.end(done);
    })();
    // `context` is unused by this fake, but keep the signature honest.
    void context;
    return stream;
  };
}

describe("assistant text delta to first visible event", () => {
  const sessions: SessionBundle["session"][] = [];
  const dirs: string[] = [];
  const fetchBlocked = vi.fn<typeof fetch>(() => {
    throw new Error("Unexpected network request in offline delta test");
  });

  beforeEach(() => {
    fetchBlocked.mockClear();
    vi.stubGlobal("fetch", fetchBlocked);
  });

  afterEach(() => {
    for (const session of sessions.splice(0)) session.dispose();
    for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
    vi.unstubAllGlobals();
    expect(fetchBlocked).not.toHaveBeenCalled();
  });

  it("publishes nothing until message_end, so first visible text trails the first delta", async () => {
    const config = makeAgentConfig(1);
    const client = new InternalClient(config);
    const agentDir = mkdtempSync(join(tmpdir(), "fg-delta-gap-"));
    dirs.push(agentDir);
    const deltaTimes: number[] = [];

    const bundle = await buildRunSession(config, client, projection(), "synthetic-run-token", {
      agentDir,
      streamOverride: deltaStream(deltaTimes),
    });
    sessions.push(bundle.session);

    // Record, per session event, whether it produced a public event and at what time.
    // Same cast convention as worker.ts: the SDK event is structurally wider than the
    // mapper's input, which only reads `type`/`message`.
    type MappableEvent = Parameters<typeof mapSessionEvent>[0];
    const published: Array<{ type: string; at: number }> = [];
    const seen: string[] = [];
    bundle.session.subscribe((event: AgentSessionEvent) => {
      seen.push(event.type);
      for (const mapped of mapSessionEvent(event as unknown as MappableEvent)) {
        published.push({ type: mapped.type, at: Date.now() });
      }
    });

    await bundle.session.prompt("Where is the blue tin?");

    // The upstream deltas really did arrive, one per part, spaced apart.
    expect(deltaTimes).toHaveLength(ANSWER_PARTS.length);
    const deltaSpan = deltaTimes[deltaTimes.length - 1]! - deltaTimes[0]!;
    expect(deltaSpan).toBeGreaterThanOrEqual(DELTA_INTERVAL_MS * (ANSWER_PARTS.length - 1));

    // The SDK does surface streaming updates to subscribers...
    expect(seen).toContain("message_update");
    // ...but the public mapping deliberately ignores them: no delta is ever
    // published, so the user cannot see text before the message completes.
    const assistantEvents = published.filter((item) => item.type === "message.assistant_added");
    expect(assistantEvents).toHaveLength(1);

    // The gap is the measured A-03 quantity: first visible text minus first delta.
    const firstVisible = assistantEvents[0]!.at;
    const gapMs = firstVisible - deltaTimes[0]!;
    expect(gapMs).toBeGreaterThanOrEqual(DELTA_INTERVAL_MS * (ANSWER_PARTS.length - 1));

    // And the published body is the complete answer, not a partial prefix.
    const mapped = mapSessionEvent({
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "text", text: ANSWER_PARTS.join("") }],
        stopReason: "stop",
      },
    });
    expect(mapped).toEqual([
      {
        type: "message.assistant_added",
        public_payload: { role: "assistant", text: ANSWER_PARTS.join("") },
      },
    ]);
  });
});
