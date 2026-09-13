/** History and compaction regressions against the installed Pi SDK, without egress. */
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
import {
  InternalClient,
  type RunContextMessage,
  type RunContextProjection,
} from "../src/client.js";
import { buildRunSession, type BuildSessionDeps, type SessionBundle } from "../src/session.js";
import { makeAgentConfig } from "./helpers.js";

const OLD_FACT = "The blue tin is kept in the attic cupboard.";
const CURRENT_QUESTION = "Where did we put the blue tin?";
// More than Pi's default 20,000 kept tokens, so the early fact must be summarized.
const RECENT_TEXT = "Recent synthetic note. ".repeat(4_000);

function persisted(id: number, role: string, text: unknown): RunContextMessage {
  return {
    id,
    role,
    content_json: { text },
    created_at: new Date(Date.UTC(2026, 8, 1, 0, 0, id)).toISOString(),
  };
}

function projection(messages: RunContextMessage[]): RunContextProjection {
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
    messages,
    next_event_seq: 1,
    provider: {
      provider_id: "3",
      provider_name: "history-test-provider",
      model: "history-test-model",
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

function longHistory(): RunContextProjection {
  return projection([
    persisted(1, "user", OLD_FACT),
    persisted(2, "assistant", "Acknowledged."),
    persisted(3, "user", RECENT_TEXT),
    persisted(4, "assistant", "Ready."),
    persisted(5, "user", CURRENT_QUESTION),
  ]);
}

function textOf(message: { role: string; content?: unknown; summary?: string }): string {
  if (message.summary !== undefined) return message.summary;
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((block): block is { type: "text"; text: string } => block.type === "text")
    .map((block) => block.text)
    .join("");
}

interface CapturedRequest {
  summary: boolean;
  model: Model<Api>;
  context: Context;
}

interface StreamScript {
  firstResponseTokens?: number;
  responseError?: string;
  summaryError?: string;
}

function offlineStream(
  requests: CapturedRequest[],
  script: StreamScript,
): NonNullable<BuildSessionDeps["streamOverride"]> {
  return (model, context) => {
    const summary =
      context.systemPrompt?.startsWith("You are a context summarization assistant.") ?? false;
    requests.push({
      summary,
      model,
      context: { ...context, messages: structuredClone(context.messages) },
    });
    const hasFact = context.messages.some((message) => textOf(message).includes(OLD_FACT));
    const hasCheckpoint = context.messages.some((message) =>
      textOf(message).includes("Saved checkpoint:"),
    );
    const text = summary
      ? "Saved checkpoint: " + (hasFact ? OLD_FACT : "No old fact in the request.")
      : hasFact && hasCheckpoint
        ? OLD_FACT
        : "Ready to continue.";
    const input =
      !summary && requests.filter((request) => !request.summary).length === 1
        ? (script.firstResponseTokens ?? 50)
        : 40;
    const errorMessage = summary ? script.summaryError : script.responseError;
    const response: AssistantMessage = {
      role: "assistant",
      content: [{ type: "text", text }],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: {
        input,
        output: 10,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: input + 10,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: errorMessage ? "error" : "stop",
      ...(errorMessage ? { errorMessage } : {}),
      timestamp: Date.now(),
    };
    const stream = createAssistantMessageEventStream();
    stream.push({ type: "start", partial: response });
    if (errorMessage) stream.push({ type: "error", reason: "error", error: response });
    else stream.push({ type: "done", reason: "stop", message: response });
    stream.end(response);
    return stream;
  };
}

describe("restored Pi history", () => {
  const sessions: SessionBundle["session"][] = [];
  const dirs: string[] = [];
  const fetchBlocked = vi.fn<typeof fetch>(() => {
    throw new Error("Unexpected network request in offline history test");
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

  async function build(context: RunContextProjection, script: StreamScript = {}) {
    const config = makeAgentConfig(1);
    const client = new InternalClient(config);
    const executeTool = vi.spyOn(client, "executeTool");
    const requests: CapturedRequest[] = [];
    const agentDir = mkdtempSync(join(tmpdir(), "fg-history-test-"));
    dirs.push(agentDir);
    const bundle = await buildRunSession(config, client, context, "synthetic-run-token", {
      agentDir,
      streamOverride: offlineStream(requests, script),
    });
    sessions.push(bundle.session);
    const events: AgentSessionEvent[] = [];
    bundle.session.subscribe((event) => events.push(event));
    return { ...bundle, requests, executeTool, sessionEvents: events };
  }

  it.each([
    { name: "empty history", messages: [], expected: [] },
    {
      name: "only the current user",
      messages: [persisted(1, "user", CURRENT_QUESTION)],
      expected: [],
    },
    {
      name: "consecutive users",
      messages: [
        persisted(1, "user", OLD_FACT),
        persisted(2, "user", "One more note."),
        persisted(3, "user", CURRENT_QUESTION),
      ],
      expected: [OLD_FACT, "One more note."],
    },
    {
      name: "identical text with distinct IDs",
      messages: [
        persisted(1, "user", "repeat"),
        persisted(2, "user", "repeat"),
        persisted(3, "user", "repeat"),
      ],
      expected: ["repeat", "repeat"],
    },
    {
      name: "duplicate history and current IDs",
      messages: [
        persisted(1, "user", OLD_FACT),
        persisted(1, "user", OLD_FACT),
        persisted(2, "assistant", "Acknowledged."),
        persisted(2, "assistant", "Acknowledged."),
        persisted(3, "user", CURRENT_QUESTION),
        persisted(3, "user", CURRENT_QUESTION),
      ],
      expected: [OLD_FACT, "Acknowledged."],
    },
    {
      name: "non-text content and unknown roles",
      messages: [
        persisted(1, "user", null),
        persisted(2, "assistant", { text: "nested" }),
        persisted(3, "toolResult", "old tool result"),
        persisted(4, "system", "old system prompt"),
        persisted(5, "user", OLD_FACT),
        persisted(6, "user", CURRENT_QUESTION),
        persisted(7, "user", undefined),
      ],
      expected: [OLD_FACT],
    },
    {
      name: "empty but valid text",
      messages: [persisted(1, "user", ""), persisted(2, "assistant", ""), persisted(3, "user", "")],
      expected: ["", ""],
    },
    {
      name: "assistant history without a current user",
      messages: [persisted(1, "assistant", "Saved reply.")],
      expected: ["Saved reply."],
    },
  ])("restores $name without side effects", async ({ messages, expected }) => {
    const original = structuredClone(messages);
    const { session, requests, executeTool, events, sessionEvents } = await build(
      projection(messages),
    );
    expect(session.sessionManager.buildSessionContext().messages.map(textOf)).toEqual(expected);
    expect(session.agent.state.messages.map(textOf)).toEqual(expected);
    expect(
      session.sessionManager.getBranch().filter((entry) => entry.type === "message"),
    ).toHaveLength(expected.length);
    expect(requests).toHaveLength(0);
    expect(executeTool).not.toHaveBeenCalled();
    expect(events.size).toBe(0);
    expect(sessionEvents).toHaveLength(0);
    expect(messages).toEqual(original);
  });

  it("prefills both the manager and model context before any new turn", async () => {
    const context = projection([
      persisted(1, "user", OLD_FACT),
      persisted(2, "assistant", "Acknowledged."),
      persisted(3, "user", CURRENT_QUESTION),
    ]);
    const { session, requests, executeTool, events } = await build(context);
    expect(session.agent.state.messages.map(textOf)).toEqual([OLD_FACT, "Acknowledged."]);
    expect(session.sessionManager.buildSessionContext().messages.map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
    ]);
    expect(
      session.sessionManager.getBranch().filter((entry) => entry.type === "message"),
    ).toHaveLength(2);
    expect(requests).toHaveLength(0);
    expect(executeTool).not.toHaveBeenCalled();
    expect(events.size).toBe(0);

    await session.prompt(CURRENT_QUESTION);
    expect(requests).toHaveLength(1);
    expect(requests[0]!.context.messages.map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
      CURRENT_QUESTION,
    ]);
  });

  it("keeps all durable messages in ID order without a recent-N truncation", async () => {
    const messages = Array.from({ length: 251 }, (_, i) =>
      persisted(i + 1, "user", "Durable note " + i),
    );
    // Creation times need not agree with durable order; they do not reorder the transcript.
    messages[0]!.created_at = "2026-09-12T00:00:00Z";
    messages[1]!.created_at = "2026-09-01T00:00:00Z";
    const expected = messages.slice(0, -1).map((message) => message.content_json["text"]);
    const { session, requests } = await build(projection(messages));
    expect(session.sessionManager.buildSessionContext().messages.map(textOf)).toEqual(expected);
    await session.prompt("Durable note 250");
    expect(requests[0]!.context.messages.map(textOf)).toEqual([...expected, "Durable note 250"]);
  });

  it("restores only allowed text, leaving old tool, RAG, thinking and provider state behind", async () => {
    const user = persisted(1, "user", OLD_FACT);
    const assistant = persisted(
      2,
      "assistant",
      "A saved answer may still contain an earlier source fact.",
    );
    assistant.content_json = {
      ...assistant.content_json,
      thinking: "old-private-thinking",
      tool_calls: [{ name: "familygraph.echo", arguments: { text: "old-tool-argument" } }],
      tool_results: [{ text: "old-tool-result" }],
      rag_blocks: [{ content: "old-rag-block" }],
      citations: [{ content: "old-citation-body" }],
      provider: "old-private-provider",
    };
    const context = projection([
      user,
      assistant,
      persisted(3, "toolResult", "old-tool-result"),
      persisted(4, "compactionSummary", "old-checkpoint"),
      persisted(5, "user", CURRENT_QUESTION),
    ]);
    context.context_blocks = [
      {
        source_id: "current-source",
        source_type: "memory",
        scope: "private",
        sensitivity: "normal",
        revision: 1,
        citation: "[R1]",
        content: "current-rag-block",
      },
    ];
    const { session, requests, executeTool } = await build(context);
    await session.prompt(CURRENT_QUESTION);
    const messages = requests[0]!.context.messages;
    expect(messages.map(textOf)).toEqual([
      OLD_FACT,
      assistant.content_json["text"],
      CURRENT_QUESTION,
    ]);
    expect(JSON.stringify(messages)).not.toMatch(/old-(private|tool|rag|citation|checkpoint)/);
    // Only the worker may inject current context blocks into the current prompt.
    expect(JSON.stringify(messages)).not.toContain("current-rag-block");
    expect(executeTool).not.toHaveBeenCalled();
  });

  it.each(["openai-completions", "openai-responses"] as const)(
    "keeps the bound %s provider snapshot while restoring history",
    async (api) => {
      const context = projection([
        persisted(1, "user", OLD_FACT),
        persisted(2, "assistant", "Saved reply."),
        persisted(3, "user", CURRENT_QUESTION),
      ]);
      context.provider!.api = api;
      context.messages[1]!.content_json["model"] = "old-model";
      const { session, requests } = await build(context);
      expect(session.model).toMatchObject({
        api,
        provider: "history-test-provider",
        id: "history-test-model",
      });
      const restored = session.agent.state.messages.find((message) => message.role === "assistant");
      expect(restored).toMatchObject({
        api,
        provider: "history-test-provider",
        model: "history-test-model",
        usage: { totalTokens: 0 },
      });
      await session.prompt(CURRENT_QUESTION);
      expect(requests[0]!.model).toMatchObject({
        api,
        provider: "history-test-provider",
        id: "history-test-model",
      });
      expect(JSON.stringify(requests[0]!.context.messages)).not.toContain("old-model");
    },
  );

  it("manual compaction summarizes the early fact and uses that summary on the next prompt", async () => {
    const { session, requests, sessionEvents } = await build(longHistory());
    const result = await session.compact();
    expect(result.summary).toContain(OLD_FACT);
    expect(requests.filter((request) => request.summary)).toHaveLength(1);
    expect(requests[0]!.context.messages.map(textOf).join("\n")).toContain(OLD_FACT);
    expect(sessionEvents).toContainEqual({ type: "compaction_start", reason: "manual" });
    expect(
      session.agent.state.messages.some(
        (message) => message.role === "user" && textOf(message) === OLD_FACT,
      ),
    ).toBe(false);

    await session.prompt(CURRENT_QUESTION);
    const continued = requests.find((request) => !request.summary)!;
    expect(continued.context.messages.map(textOf).join("\n")).toContain(
      "Saved checkpoint: " + OLD_FACT,
    );
    expect(textOf(session.agent.state.messages.at(-1)!)).toBe(OLD_FACT);
  });

  it("automatically compacts after a real prompt crosses the usage threshold", async () => {
    const context = longHistory();
    // Initial text estimate is ~23k; 30,010 reported tokens crosses 40k - 16,384.
    context.provider!.context_window = 40_000;
    const { session, requests, sessionEvents } = await build(context, {
      firstResponseTokens: 30_000,
    });
    expect(session.autoCompactionEnabled).toBe(true);
    expect(session.settingsManager.getCompactionSettings()).toEqual({
      enabled: true,
      reserveTokens: 16_384,
      keepRecentTokens: 20_000,
    });

    await session.prompt(CURRENT_QUESTION);
    expect(sessionEvents).toContainEqual({ type: "compaction_start", reason: "threshold" });
    expect(sessionEvents).toContainEqual(
      expect.objectContaining({
        type: "compaction_end",
        reason: "threshold",
        aborted: false,
        willRetry: false,
        result: expect.objectContaining({ summary: expect.stringContaining(OLD_FACT) }),
      }),
    );
    const summaryRequest = requests.find((request) => request.summary)!;
    expect(summaryRequest.context.messages.map(textOf).join("\n")).toContain(OLD_FACT);
    expect(
      session.agent.state.messages.some(
        (message) => message.role === "user" && textOf(message) === OLD_FACT,
      ),
    ).toBe(false);

    await session.prompt("Please recall that location once more.");
    const continued = requests.filter((request) => !request.summary).at(-1)!;
    expect(continued.context.messages.map(textOf).join("\n")).toContain(
      "Saved checkpoint: " + OLD_FACT,
    );
    expect(textOf(session.agent.state.messages.at(-1)!)).toBe(OLD_FACT);
    expect(sessionEvents.filter((event) => event.type === "compaction_start")).toHaveLength(1);
  });

  it("automatically compacts restored zero-usage history before submitting the current prompt", async () => {
    const context = longHistory();
    context.provider!.context_window = 38_000;
    const { session, requests, sessionEvents } = await build(context);
    expect(requests).toHaveLength(0);
    await session.prompt(CURRENT_QUESTION);
    expect(requests.map((request) => request.summary)).toEqual([true, false]);
    expect(requests[0]!.context.messages.map(textOf).join("\n")).toContain(OLD_FACT);
    expect(requests[0]!.context.messages.map(textOf).join("\n")).not.toContain(CURRENT_QUESTION);
    expect(
      requests[1]!.context.messages.map(textOf).filter((text) => text === CURRENT_QUESTION),
    ).toHaveLength(1);
    expect(requests[1]!.context.messages.map(textOf).join("\n")).toContain(
      "Saved checkpoint: " + OLD_FACT,
    );
    expect(sessionEvents).toContainEqual({ type: "compaction_start", reason: "threshold" });
  });

  it("reports an overflow compaction failure without silently deleting durable history", async () => {
    const context = longHistory();
    const original = structuredClone(context);
    const { session, requests, sessionEvents } = await build(context, {
      responseError: "maximum context length exceeded",
      summaryError: "Synthetic summarizer refusal",
    });
    await session.prompt(CURRENT_QUESTION);
    expect(requests.map((request) => request.summary)).toEqual([false, true]);
    expect(requests[0]!.context.messages.map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
      RECENT_TEXT,
      "Ready.",
      CURRENT_QUESTION,
    ]);
    expect(sessionEvents).toContainEqual(
      expect.objectContaining({
        type: "compaction_end",
        reason: "overflow",
        aborted: false,
        willRetry: false,
        result: undefined,
        errorMessage:
          "Context overflow recovery failed: Summarization failed: Synthetic summarizer refusal",
      }),
    );
    expect(session.sessionManager.getBranch().some((entry) => entry.type === "compaction")).toBe(
      false,
    );
    expect(session.sessionManager.buildSessionContext().messages.slice(0, 4).map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
      RECENT_TEXT,
      "Ready.",
    ]);
    expect(context).toEqual(original);
  });

  it("rebuilds a failed attempt from durable text in a fresh manager", async () => {
    const context = longHistory();
    const original = structuredClone(context);
    const first = await build(context, { responseError: "Synthetic rejected request" });
    await first.session.prompt(CURRENT_QUESTION);
    expect(first.session.agent.state.messages.at(-1)).toMatchObject({ stopReason: "error" });

    const second = await build({ ...context, attempt: 2 });
    expect(second.session.sessionManager).not.toBe(first.session.sessionManager);
    expect(second.session.sessionManager.buildSessionContext().messages.map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
      RECENT_TEXT,
      "Ready.",
    ]);
    expect(second.requests).toHaveLength(0);
    await second.session.prompt(CURRENT_QUESTION);
    expect(
      second.requests[0]!.context.messages.map(textOf).filter((text) => text === CURRENT_QUESTION),
    ).toHaveLength(1);
    expect(JSON.stringify(second.requests[0]!.context.messages)).not.toContain(
      "Synthetic rejected request",
    );
    expect(context).toEqual(original);
  });

  it("does not carry a compacted in-memory checkpoint into a rebuilt run or another session", async () => {
    const context = longHistory();
    const first = await build(context);
    await first.session.compact();
    expect(
      first.session.sessionManager.getBranch().some((entry) => entry.type === "compaction"),
    ).toBe(true);

    const rebuilt = await build(context);
    expect(
      rebuilt.session.sessionManager.getBranch().some((entry) => entry.type === "compaction"),
    ).toBe(false);
    expect(rebuilt.session.agent.state.messages.map(textOf)).toEqual([
      OLD_FACT,
      "Acknowledged.",
      RECENT_TEXT,
      "Ready.",
    ]);

    const otherContext = projection([persisted(1, "user", "Another account's current question.")]);
    Object.assign(otherContext, { account_id: "99", space_id: "88", session_id: "77" });
    const other = await build(otherContext);
    expect(other.session.sessionManager).not.toBe(first.session.sessionManager);
    expect(other.session.agent.state.messages).toEqual([]);
    expect(other.session.sessionManager.buildSessionContext().messages).toEqual([]);
  });
});
