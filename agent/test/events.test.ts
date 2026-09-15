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

  it("keeps an empty answer without tool calls (boundary of the filter)", () => {
    expect(
      mapSessionEvent({
        type: "message_end",
        message: { role: "assistant", content: [{ type: "text", text: "" }], stopReason: "stop" },
      }),
    ).toEqual([
      { type: "message.assistant_added", public_payload: { role: "assistant", text: "" } },
    ]);
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
