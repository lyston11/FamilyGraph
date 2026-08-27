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

  it("drops malformed or non-external citations and never leaks raw tool results", () => {
    const buffer = new RunEventBuffer();
    buffer.onSessionEvent({
      type: "tool_execution_end",
      toolCallId: "tc_bad",
      toolName: "familygraph.fetch_approved_page",
      result: { citation: { url: "https://x", title: "t", excerpt: "e", fetched_at: "d", trust: "internal" } },
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
