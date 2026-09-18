import { describe, expect, it } from "vitest";
import { RunEventBuffer, isKnownEventType, mapSessionEvent } from "../src/events.js";

describe("event type registry", () => {
  it("contains exactly the V2.1 registry", () => {
    expect(isKnownEventType("run.started")).toBe(true);
    expect(isKnownEventType("tool.execution.completed")).toBe(true);
    expect(isKnownEventType("card.show")).toBe(false);
    expect(isKnownEventType("message.update")).toBe(false);
  });
});

describe("mapSessionEvent", () => {
  it("maps lifecycle events to whitelisted payloads", () => {
    expect(mapSessionEvent({ type: "agent_start" })).toEqual([
      { type: "run.started", public_payload: {} },
    ]);
    expect(mapSessionEvent({ type: "turn_start" })).toEqual([
      { type: "turn.started", public_payload: {} },
    ]);
    expect(mapSessionEvent({ type: "turn_end" })).toEqual([
      { type: "turn.completed", public_payload: {} },
    ]);
  });

  it("projects assistant messages to role+text only (no provider payload)", () => {
    const mapped = mapSessionEvent({
      type: "message_end",
      message: {
        role: "assistant",
        content: [
          { type: "thinking", thinking: "secret reasoning" },
          { type: "text", text: "hello " },
          { type: "text", text: "world" },
        ],
        usage: { input: 99 },
        provider: "openai",
        model: "gpt-x",
        stopReason: "stop",
      },
    });
    expect(mapped).toHaveLength(1);
    expect(mapped[0]).toEqual({
      type: "message.assistant_added",
      public_payload: { role: "assistant", text: "hello world" },
    });
    const serialized = JSON.stringify(mapped);
    expect(serialized).not.toContain("secret reasoning");
    expect(serialized).not.toContain("usage");
    expect(serialized).not.toContain("gpt-x");
  });

  it("drops a tool-only assistant message that has no prose", () => {
    expect(
      mapSessionEvent({
        type: "message_end",
        message: {
          role: "assistant",
          content: [
            { type: "toolCall", id: "tc_1", name: "familygraph.echo", arguments: { text: "hi" } },
          ],
          stopReason: "toolUse",
        },
      }),
    ).toEqual([]);
  });

  it("keeps an assistant message that answers while calling a tool", () => {
    expect(
      mapSessionEvent({
        type: "message_end",
        message: {
          role: "assistant",
          content: [
            { type: "text", text: "let me check" },
            { type: "toolCall", id: "tc_1", name: "familygraph.echo", arguments: {} },
          ],
          stopReason: "toolUse",
        },
      }),
    ).toEqual([
      {
        type: "message.assistant_added",
        public_payload: { role: "assistant", text: "let me check" },
      },
    ]);
  });

  it("drops an empty answer without tool calls (no displayable content)", () => {
    expect(
      mapSessionEvent({
        type: "message_end",
        message: { role: "assistant", content: [{ type: "text", text: "" }], stopReason: "stop" },
      }),
    ).toEqual([]);
  });

  it("ignores user-role message_end and streaming updates", () => {
    expect(
      mapSessionEvent({ type: "message_end", message: { role: "user", content: [] } }),
    ).toEqual([]);
    expect(mapSessionEvent({ type: "message_update" })).toEqual([]);
    expect(mapSessionEvent({ type: "agent_settled" })).toEqual([]);
  });

  it("tool events carry ids/flags but never raw results", () => {
    const started = mapSessionEvent({
      type: "tool_execution_start",
      toolCallId: "tc_1",
      toolName: "familygraph.echo",
      args: { text: "hi" },
    });
    expect(started[0]!.type).toBe("tool.execution.started");
    const completed = mapSessionEvent({
      type: "tool_execution_end",
      toolCallId: "tc_1",
      toolName: "familygraph.echo",
      result: { echoed: "SENSITIVE-RESULT" },
      isError: false,
    });
    expect(completed[0]!.type).toBe("tool.execution.completed");
    expect(JSON.stringify(completed)).not.toContain("SENSITIVE-RESULT");
  });

  it("normalizes provider wire names to canonical public event names", () => {
    expect(
      mapSessionEvent({
        type: "tool_execution_start",
        toolCallId: "tc_wire",
        toolName: "familygraph_get_self_context",
      }),
    ).toEqual([
      {
        type: "tool.execution.started",
        public_payload: {
          tool_call_id: "tc_wire",
          tool_name: "familygraph.get_self_context",
          tool_version: 1,
        },
      },
    ]);
    expect(
      mapSessionEvent({
        type: "tool_execution_end",
        toolCallId: "tc_wire",
        toolName: "familygraph_get_self_context",
        isError: false,
      })[0]!.public_payload,
    ).toMatchObject({ tool_name: "familygraph.get_self_context" });
  });
});

describe("RunEventBuffer", () => {
  it("assigns monotonic per-run seq and drains in order", () => {
    const buffer = new RunEventBuffer();
    buffer.push("run.started", {});
    buffer.onSessionEvent({ type: "agent_start" }); // would duplicate run.started
    buffer.push("message.user_added", { role: "user", text: "hi" });
    const drained = buffer.drain();
    expect(drained.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(drained.map((e) => e.type)).toEqual([
      "run.started",
      "run.started",
      "message.user_added",
    ]);
    expect(buffer.size).toBe(0);
  });

  it("attaches fetch_approved_page citations to the next assistant message", () => {
    const buffer = new RunEventBuffer();
    buffer.onSessionEvent({
      type: "tool_execution_end",
      toolCallId: "tc_w",
      toolName: "familygraph.fetch_approved_page",
      result: {
        content: "page text",
        citation: {
          url: "https://www.example.com/page",
          title: "Example",
          excerpt: "bounded excerpt",
          fetched_at: "2026-08-27T00:00:00Z",
          trust: "external",
        },
      },
      isError: false,
    });
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "answer" }] },
    });
    const drained = buffer.drain();
    const assistant = drained.find((e) => e.type === "message.assistant_added")!;
    expect(assistant.public_payload).toEqual({
      role: "assistant",
      text: "answer",
      web_citations: [
        {
          url: "https://www.example.com/page",
          title: "Example",
          excerpt: "bounded excerpt",
          fetched_at: "2026-08-27T00:00:00Z",
          trust: "external",
        },
      ],
    });
  });

  it("recognizes fetch citations when Pi reports the provider wire name", () => {
    const buffer = new RunEventBuffer();
    buffer.onSessionEvent({
      type: "tool_execution_end",
      toolCallId: "tc_wire_web",
      toolName: "familygraph_fetch_approved_page",
      result: {
        citation: {
          url: "https://www.example.com/wire",
          title: "Wire",
          excerpt: "wire excerpt",
          fetched_at: "2026-08-30T00:00:00Z",
          trust: "external",
        },
      },
      isError: false,
    });
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "answer" }] },
    });
    expect(
      buffer.drain().find((event) => event.type === "message.assistant_added")?.public_payload,
    ).toMatchObject({
      web_citations: [expect.objectContaining({ url: "https://www.example.com/wire" })],
    });
  });

  it("drops malformed or non-external citations and never leaks raw tool results", () => {
    const buffer = new RunEventBuffer();
    buffer.onSessionEvent({
      type: "tool_execution_end",
      toolCallId: "tc_bad",
      toolName: "familygraph.fetch_approved_page",
      result: {
        citation: {
          url: "https://x",
          title: "t",
          excerpt: "e",
          fetched_at: "d",
          trust: "internal",
        },
      },
      isError: false,
    });
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "answer" }] },
    });
    const drained = buffer.drain();
    const assistant = drained.find((e) => e.type === "message.assistant_added")!;
    expect(assistant.public_payload).toEqual({ role: "assistant", text: "answer" });
    expect(JSON.stringify(drained)).not.toContain("https://x");
  });
});


describe("private context references", () => {
  it("binds only completed assistant text and only handles actually used", () => {
    const used = "rag:7:r1:c9";
    const unused = "rag:8:r1:c10";
    const buffer = new RunEventBuffer(4, { build_id: 31, attempt: 2, allowed_handles: [used, unused] });
    const message = (text: string, stopReason: string) => ({
      role: "assistant", stopReason, content: [{ type: "text", text }],
    });
    expect(buffer.onSessionEvent({ type: "message_update", message: message(`[${unused}]`, "stop") })).toBe(0);
    buffer.onSessionEvent({ type: "message_end", message: message(`[${unused}]`, "error") });
    buffer.onSessionEvent({ type: "message_end", message: message(`[${used}] [${used}] [rag:forged:r1:c99]`, "stop") });
    const [errored, completed] = buffer.drain();
    expect(errored?.context_reference).toBeUndefined();
    expect(completed?.context_reference).toEqual({ build_id: 31, attempt: 2, used_handles: [used] });
    expect(completed?.public_payload).toEqual({ role: "assistant", text: `[${used}] [${used}] [rag:forged:r1:c99]` });
    expect(Object.keys(completed?.public_payload ?? {})).toEqual(["role", "text"]);
  });

  it("does not turn a legacy no-build answer or tool transcript into a reference", () => {
    const buffer = new RunEventBuffer();
    buffer.onSessionEvent({ type: "message_end", message: {
      role: "assistant", stopReason: "stop", content: [{ type: "text", text: "[rag:7:r1:c9]" }],
    } });
    expect(buffer.drain()[0]?.context_reference).toBeUndefined();
  });
});

describe("producer stage timing", () => {
  /**
   * The backend's `created_at` is the persistence time and the flusher batches
   * every 250ms, so a 125ms tool call sharing a batch with its end event shows
   * ~1ms. These tests pin the source-clock contract that replaces it: each
   * event carries the monotonic duration of the stage ending at that event.
   */
  const clock = (start = 0) => {
    let current = start;
    return { now: () => current, advance: (ms: number) => (current += ms) };
  };

  it("measures prep, turn generation and tool execution from the monotonic clock", () => {
    const time = clock(1_000);
    const buffer = new RunEventBuffer(
      1,
      undefined,
      { prepStartedAt: 1_000, now: time.now },
    );
    time.advance(4_200); // context fetch + session creation
    buffer.onSessionEvent({ type: "agent_start" });
    time.advance(30); // SDK internals before turn_start
    buffer.onSessionEvent({ type: "turn_start" });
    time.advance(20_000); // model generation for turn 1
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "a" }], stopReason: "stop" },
    });
    buffer.onSessionEvent({ type: "turn_end" });
    buffer.onSessionEvent({ type: "tool_execution_start", toolCallId: "tc_1", toolName: "familygraph.echo" });
    time.advance(125); // real tool work, well below the 250ms flush interval
    buffer.onSessionEvent({ type: "tool_execution_end", toolCallId: "tc_1", toolName: "familygraph.echo" });

    const events = buffer.drain();
    const byType = (type: string) => events.find((event) => event.type === type);
    expect(byType("run.started")?.timing).toEqual({ source: "sidecar-v1", duration_ms: 4_200 });
    expect(byType("message.assistant_added")?.timing).toEqual({
      source: "sidecar-v1",
      duration_ms: 20_000,
    });
    expect(byType("tool.execution.completed")?.timing).toEqual({
      source: "sidecar-v1",
      duration_ms: 125,
    });
  });

  it("pairs each turn with its own generation and ignores tool-only turns", () => {
    const time = clock(0);
    const buffer = new RunEventBuffer(1, undefined, { prepStartedAt: 0, now: time.now });
    buffer.onSessionEvent({ type: "turn_start" });
    time.advance(1_000);
    buffer.onSessionEvent({
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "toolCall", id: "tc_1", name: "familygraph.echo", arguments: {} }],
        stopReason: "toolUse",
      },
    });
    buffer.onSessionEvent({ type: "turn_end" });
    // Turn 2 produces the answer; it must not inherit turn 1's origin.
    buffer.onSessionEvent({ type: "turn_start" });
    time.advance(3_000);
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "b" }], stopReason: "stop" },
    });

    const answered = buffer.drain().filter((event) => event.type === "message.assistant_added");
    expect(answered).toHaveLength(1);
    expect(answered[0]?.timing).toEqual({ source: "sidecar-v1", duration_ms: 3_000 });
  });

  it("omits timing instead of fabricating zero when an origin is missing", () => {
    const buffer = new RunEventBuffer();
    // No prep origin injected: the backend must read this stage as unknown.
    buffer.onSessionEvent({ type: "agent_start" });
    // Tool end without a paired start must not become a 0ms sample.
    buffer.onSessionEvent({ type: "tool_execution_end", toolCallId: "orphan", toolName: "familygraph.echo" });
    const [started, completed] = buffer.drain();
    expect(started?.timing).toBeUndefined();
    expect(completed?.timing).toBeUndefined();
  });

  it("never puts timing into the public payload", () => {
    const time = clock(0);
    const buffer = new RunEventBuffer(1, undefined, { prepStartedAt: 0, now: time.now });
    time.advance(50);
    buffer.onSessionEvent({ type: "agent_start" });
    const [started] = buffer.drain();
    expect(started?.public_payload).toEqual({});
    expect(Object.keys(started ?? {})).toEqual(["seq", "type", "public_payload", "timing"]);
  });

  it("reports SDK compaction as a sub-component of the turn it happened in", () => {
    const time = clock(0);
    const buffer = new RunEventBuffer(1, undefined, { prepStartedAt: 0, now: time.now });
    buffer.onSessionEvent({ type: "turn_start" });
    time.advance(2_000);
    // Summarization request inside the turn: counted in the turn AND reported
    // separately, so the backend never reads model_turn as pure generation.
    buffer.onSessionEvent({ type: "compaction_start", reason: "threshold" });
    time.advance(8_000);
    buffer.onSessionEvent({ type: "compaction_end", reason: "threshold", aborted: false, willRetry: false });
    time.advance(10_000);
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "a" }], stopReason: "stop" },
    });
    const answer = buffer.drain().find((event) => event.type === "message.assistant_added");
    expect(answer?.timing).toEqual({
      source: "sidecar-v1",
      duration_ms: 20_000,
      compaction_ms: 8_000,
    });
  });

  it("does not attribute a previous turn's compaction to the next turn", () => {
    const time = clock(0);
    const buffer = new RunEventBuffer(1, undefined, { prepStartedAt: 0, now: time.now });
    buffer.onSessionEvent({ type: "turn_start" });
    buffer.onSessionEvent({ type: "compaction_start", reason: "threshold" });
    time.advance(5_000);
    buffer.onSessionEvent({ type: "compaction_end", reason: "threshold", aborted: false, willRetry: false });
    time.advance(1_000);
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "a" }], stopReason: "stop" },
    });
    buffer.onSessionEvent({ type: "turn_end" });
    // Turn 2 has no compaction: it must not inherit turn 1's 5s.
    buffer.onSessionEvent({ type: "turn_start" });
    time.advance(3_000);
    buffer.onSessionEvent({
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "b" }], stopReason: "stop" },
    });
    const answers = buffer.drain().filter((event) => event.type === "message.assistant_added");
    expect(answers).toHaveLength(2);
    expect(answers[0]?.timing?.compaction_ms).toBe(5_000);
    expect(answers[1]?.timing?.compaction_ms).toBeUndefined();
  });
});
