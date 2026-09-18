/**
 * A-03 / 09-18 P0-2: the gap between upstream assistant text deltas and the
 * first *visible* assistant event, against the real Pi SDK, with no egress.
 *
 * The product contract (spec/backend/agent-runtime.md §4) is that live prose is
 * published as provisional `assistant.text_delta` fragments while the turn
 * streams, and that `message_end` still publishes the authoritative
 * `message.assistant_added` whose text is the complete answer. This test makes
 * both halves executable: it fails if deltas stop being published (the original
 * 10-30s blank wait) or if the authoritative message stops carrying the full
 * answer / starts duplicating the provisional text.
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
import { RunEventBuffer, mapSessionEvent } from "../src/events.js";
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

  it("publishes provisional prose as it streams, then the authoritative full text", async () => {
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
    const buffer = new RunEventBuffer(1);
    bundle.session.subscribe((event: AgentSessionEvent) => {
      seen.push(event.type);
      for (const mapped of mapSessionEvent(event as unknown as MappableEvent)) {
        published.push({ type: mapped.type, at: Date.now() });
      }
      buffer.onSessionEvent(event as unknown as MappableEvent);
    });

    await bundle.session.prompt("Where is the blue tin?");

    // The upstream deltas really did arrive, one per part, spaced apart.
    expect(deltaTimes).toHaveLength(ANSWER_PARTS.length);
    const deltaSpan = deltaTimes[deltaTimes.length - 1]! - deltaTimes[0]!;
    expect(deltaSpan).toBeGreaterThanOrEqual(DELTA_INTERVAL_MS * (ANSWER_PARTS.length - 1));

    // The SDK surfaces streaming updates, and the public mapping no longer
    // discards them: the reader sees prose before the message completes.
    expect(seen).toContain("message_update");
    const events = buffer.drain();
    const deltas = events.filter((event) => event.type === "assistant.text_delta");
    expect(deltas.length).toBeGreaterThan(0);

    // Provisional fragments concatenate to exactly the streamed answer — no
    // duplication and no dropped text, because the buffer coalesces rather than
    // rewriting.
    const provisional = deltas
      .map((event) => (event.public_payload as { delta: string }).delta)
      .join("");
    expect(provisional).toBe(ANSWER_PARTS.join(""));

    // The authoritative message still arrives exactly once, carrying the full
    // answer; the client replaces the provisional text with it rather than
    // appending (so the two must not be summed by a consumer).
    const assistantEvents = events.filter((event) => event.type === "message.assistant_added");
    expect(assistantEvents).toHaveLength(1);
    expect(assistantEvents[0]!.public_payload).toEqual({
      role: "assistant",
      text: ANSWER_PARTS.join(""),
    });

    // Provisional prose precedes the authoritative message in stream order.
    const seqOf = (type: string): number =>
      events.find((event) => event.type === type)!.seq;
    expect(seqOf("assistant.text_delta")).toBeLessThan(seqOf("message.assistant_added"));

    // The gap the A-03 baseline measured is now bounded by the coalescing
    // window, not by the whole generation.
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

  it("records first_text_ms well below duration_ms for the same turn (LL-AC1)", async () => {
    // The live baseline could not separate "the model took 33s" from "prose was
    // ready long before the user saw it". This asserts the new source timing
    // separates them against the real SDK: the first text delta lands at the
    // first DELTA_INTERVAL_MS, while duration_ms spans the whole message.
    const config = makeAgentConfig(1);
    const client = new InternalClient(config);
    const agentDir = mkdtempSync(join(tmpdir(), "fg-first-text-"));
    dirs.push(agentDir);

    const bundle = await buildRunSession(config, client, projection(), "synthetic-run-token", {
      agentDir,
      streamOverride: deltaStream([]),
    });
    sessions.push(bundle.session);

    const buffer = new RunEventBuffer(1);
    bundle.session.subscribe((event: AgentSessionEvent) => {
      // Same cast convention as worker.ts; the mapper only reads type/message.
      buffer.onSessionEvent(event as unknown as Parameters<typeof mapSessionEvent>[0]);
    });

    await bundle.session.prompt("Where is the blue tin?");

    const answers = buffer
      .drain()
      .filter((event) => event.type === "message.assistant_added");
    expect(answers).toHaveLength(1);
    const timing = answers[0]!.timing!;

    // Both quantities exist and are ordered, because the deltas really streamed.
    expect(timing.first_text_ms).toBeDefined();
    expect(timing.duration_ms).toBeGreaterThan(timing.first_text_ms!);
    // The visible gap is at least the time the remaining deltas took to arrive.
    expect(timing.duration_ms - timing.first_text_ms!).toBeGreaterThanOrEqual(
      DELTA_INTERVAL_MS * (ANSWER_PARTS.length - 1) - 20,
    );
    // No retry happened on a clean stream, so the retry sub-components stay absent
    // rather than being reported as a measured zero.
    expect(timing.retry_count).toBeUndefined();
    expect(timing.retry_wait_ms).toBeUndefined();
  });
});
