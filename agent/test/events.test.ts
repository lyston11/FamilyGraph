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
});
