/**
 * Full worker-cycle integration test.
 *
 * A mock FastAPI (node:http) implements the AUTHORITATIVE internal protocol
 * exactly as backend/app/schemas/agent.py defines it (strict extra=forbid):
 * flat lease responses with numeric ids + attempt/tool_allowlist/policy_version,
 * HTTP 204 on empty queue, ContextOut-shaped projections, /events/append with
 * {seq,event_id} acceptances, tool bodies {version,input,tool_call_id}, and
 * /settle with error_code+error splitting. The worker drives a REAL Pi
 * AgentSession whose provider stream is scripted offline via
 * pi.registerProvider({streamSimple}). No network, no model service.
 */

import { createServer, type IncomingMessage, type Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type AssistantMessageEventStream,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import {
  InternalClient,
  type ProviderPolicyResult,
  type RunContextBlock,
  type RunContextMessage,
} from "../src/client.js";
import type { AgentConfig } from "../src/config.js";
import { createLogger } from "../src/logger.js";
import { providerWireName } from "../src/tools.js";
import { SidecarWorker } from "../src/worker.js";

// ---------------------------------------------------------------------------
// Shared mock state + mock FastAPI (authoritative backend shapes)
// ---------------------------------------------------------------------------

interface MockJob {
  job_id: number;
  run_id: number;
  agent_kind: "assistant" | "unexpected";
  /** Attempt counter, incremented by the mock at each lease like the backend. */
  attempt: number;
  tool_allowlist: string[];
  policy_version: string;
  run_token: string;
  allowlist: string[];
  leasedAt?: number;
  /** Override for the context projection's provider resolution. */
  provider?: Record<string, unknown>;
  messages?: RunContextMessage[];
  contextBlocks?: RunContextBlock[];
  contextBuildId?: number;
}

interface MockState {
  jobs: MockJob[];
  /** Keyed by String(run_id). */
  eventsByRun: Map<string, FgWireEvent[]>;
  toolCalls: Array<{ run_id: string; tool: string; body: Record<string, unknown> }>;
  settles: Array<{
    run_id: string;
    status: string;
    error_code?: unknown;
    error?: unknown;
  }>;
  cancelOnNextHeartbeat: boolean;
  heartbeatStatus?: number;
}

const state: MockState = {
  jobs: [],
  eventsByRun: new Map(),
  toolCalls: [],
  settles: [],
  cancelOnNextHeartbeat: false,
};

let idCounter = 0;
const CURRENT_PROMPT = "Please echo 'ping' then say done.";
const HISTORY_FACT = "The blue tin is kept in the attic cupboard.";

function historyMessages(): RunContextMessage[] {
  return [
    {
      id: 1,
      role: "user",
      content_json: { text: HISTORY_FACT },
      created_at: "2026-09-01T00:00:00Z",
    },
    {
      id: 2,
      role: "assistant",
      created_at: "2026-09-01T00:00:01Z",
      content_json: {
        text: "Acknowledged.",
        thinking: "old-private-thinking",
        tool_results: [{ text: "old-tool-result" }],
        citations: [{ content: "old-rag-citation" }],
      },
    },
    {
      id: 3,
      role: "user",
      content_json: { text: CURRENT_PROMPT },
      created_at: "2026-09-01T00:00:02Z",
    },
  ];
}

function compactionHistoryMessages(): RunContextMessage[] {
  const history = historyMessages();
  return [
    ...history.slice(0, 2),
    {
      id: 3,
      role: "user",
      content_json: { text: "Recent synthetic note. ".repeat(4_000) },
      created_at: "2026-09-01T00:00:02Z",
    },
    {
      id: 4,
      role: "assistant",
      content_json: { text: "Ready." },
      created_at: "2026-09-01T00:00:03Z",
    },
    { ...history[2]!, id: 5 },
  ];
}

function makeConfig(port: number): AgentConfig {
  return {
    apiBaseUrl: `http://127.0.0.1:${port}`,
    internalApiBaseUrl: `http://127.0.0.1:${port}`,
    providerStreamMaxRetries: 0,
    providerStreamMaxRetryDelayMs: 1000,
    serviceSecret: "integration-service-secret",
    sidecarId: "sc-it",
    healthPort: 0,
    leasePollIntervalMs: 5,
    defaultLeaseMs: 60_000,
    eventFlushIntervalMs: 15,
    eventFlushBatchSize: 8,
    retryMaxAttempts: 3,
    retryBaseDelayMs: 1,
    retryMaxDelayMs: 2,
    requestTimeoutMs: 5000,
    providers: {
      cloud: {
        kind: "openai_compatible",
        baseUrl: "https://cloud.example.internal/v1",
        apiKey: "sk-integration-cloud-key",
        model: "unused-context-supplies-the-model",
      },
      local: {
        kind: "local",
        baseUrl: "http://127.0.0.1:11434/v1",
        apiKey: undefined,
        model: "llama-test",
      },
    },
  };
}

async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const raw = Buffer.concat(chunks).toString();
  return raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
}

function startMockFastAPI(): Promise<{ server: Server; port: number }> {
  let eventIdCounter = 1000;
  const server = createServer((req, res) => {
    void (async () => {
      const url = req.url ?? "";
      const auth = String(req.headers["authorization"] ?? "");
      const respond = (status: number, body: unknown): void => {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(body));
      };
      if (url === "/internal/agent/jobs/lease" && req.method === "POST") {
        // Lease accepts only HMAC-shaped service tokens.
        if (!/^Bearer ey[\w-]+\.[\w-]+\.[\w-]+$/.test(auth)) {
          return respond(401, { detail: "bad service token" });
        }
        const body = await readBody(req);
        // Strict schema: Assistant sidecars explicitly request assistant jobs.
        if (!("leased_by" in body) || body.kind !== "assistant" || "sidecar_id" in body) {
          return respond(422, { detail: "lease body must include kind=assistant and leased_by" });
        }
        const next = state.jobs.find((j) => j.leasedAt === undefined);
        if (!next) {
          // Empty queue: HTTP 204 with an EMPTY body (authoritative contract).
          res.writeHead(204);
          res.end();
          return;
        }
        next.leasedAt = Date.now();
        next.attempt += 1;
        return respond(200, {
          job_id: next.job_id,
          run_id: next.run_id,
          agent_kind: next.agent_kind,
          attempt: next.attempt,
          tool_allowlist: next.tool_allowlist,
          policy_version: next.policy_version,
          run_token: next.run_token,
        });
      }
      const job = state.jobs.find(
        (j) => j.leasedAt !== undefined && auth === `Bearer ${j.run_token}`,
      );
      if (job === undefined) return respond(401, { detail: "unknown or unleased run token" });

      if (req.method === "POST" && url === `/internal/agent/jobs/${job.job_id}/heartbeat`) {
        if (state.heartbeatStatus !== undefined) {
          const status = state.heartbeatStatus;
          state.heartbeatStatus = undefined;
          return respond(status, { detail: "lease no longer valid" });
        }
        const body = await readBody(req);
        if (Object.keys(body).length > 0) return respond(422, { detail: "heartbeat body is {}" });
        const cancelRequested = state.cancelOnNextHeartbeat;
        state.cancelOnNextHeartbeat = false;
        return respond(200, {
          ok: true,
          lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
          cancel_requested: cancelRequested,
        });
      }
      if (req.method === "GET" && url === `/internal/agent/runs/${job.run_id}/context`) {
        return respond(200, contextProjection(job));
      }
      if (req.method === "POST" && url.endsWith("/events/append")) {
        const body = (await readBody(req)) as { events?: Array<FgWireEvent> };
        const seen = state.eventsByRun.get(String(job.run_id)) ?? [];
        const accepted: Array<{ seq: number; event_id: number }> = [];
        const duplicates: number[] = [];
        for (const event of body.events ?? []) {
          if (seen.some((s) => s.seq === event.seq)) duplicates.push(event.seq);
          else {
            seen.push(event);
            accepted.push({ seq: event.seq, event_id: ++eventIdCounter });
          }
        }
        state.eventsByRun.set(String(job.run_id), seen);
        return respond(200, { accepted, duplicates });
      }
      const toolMatch = /\/internal\/agent\/runs\/(\d+)\/tools\/([\w.]+)\/execute/.exec(url);
      if (req.method === "POST" && toolMatch) {
        const toolName = toolMatch[2]!;
        const body = await readBody(req);
        // Authoritative tool body: {version, input, tool_call_id?}; the legacy
        // tool_version field must not be sent.
        if (!("version" in body) || !("input" in body) || "tool_version" in body) {
          return respond(422, { detail: "tool body must be {version, input, tool_call_id?}" });
        }
        if (!job.allowlist.includes(toolName)) {
          return respond(403, { detail: `tool ${toolName} not allowed` });
        }
        state.toolCalls.push({
          run_id: String(job.run_id),
          tool: toolName,
          body,
        });
        const input = body.input as { text?: string };
        if (toolName === "familygraph.echo") {
          return respond(200, {
            ok: true,
            tool: toolName,
            version: body.version,
            output: { text: input?.text ?? "" },
          });
        }
        if (toolName === "familygraph.probe_scope") {
          return respond(200, {
            ok: true,
            tool: toolName,
            version: body.version,
            output: {
              run_id: job.run_id,
              agent_kind: job.agent_kind,
              account_id: 900,
              space_id: 800,
              policy_version: job.policy_version,
              attempt: job.attempt,
            },
          });
        }
        if (toolName === "familygraph.list_visible_people") {
          // Visibility-projected shape as the V2.2 AgentQueryService would return.
          const listInput = body.input as { query?: string };
          return respond(200, {
            ok: true,
            tool: toolName,
            version: body.version,
            output: {
              people: [{ user_id: 12, name: "王明", fact_state: "confirmed" }],
              next_cursor: null,
              ...(listInput?.query !== undefined ? { query: listInput.query } : {}),
            },
          });
        }
        return respond(200, { ok: true, tool: toolName, version: body.version, output: {} });
      }
      if (req.method === "POST" && url === `/internal/agent/runs/${job.run_id}/settle`) {
        const body = await readBody(req);
        state.settles.push({
          run_id: String(job.run_id),
          status: String(body.status ?? "unknown"),
          error_code: body.error_code,
          error: body.error,
        });
        // Backend /settle writes the terminal event (run.settled/run.failed) at
        // the next seq; the sidecar must not emit it. Mirror that ownership here.
        const seen = state.eventsByRun.get(String(job.run_id)) ?? [];
        const nextSeq = seen.reduce((m, e) => Math.max(m, e.seq), -1) + 1;
        seen.push({
          seq: nextSeq,
          type: body.status === "failed" ? "run.failed" : "run.settled",
          public_payload:
            body.status === "failed" && body.error_code !== undefined
              ? { status: body.status, error_code: String(body.error_code) }
              : { status: body.status },
        });
        state.eventsByRun.set(String(job.run_id), seen);
        return respond(200, {
          ok: true,
          run_id: job.run_id,
          status: body.status,
          settled_at: new Date().toISOString(),
        });
      }
      respond(404, { detail: `no route ${req.method} ${url}` });
    })();
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, port: (server.address() as { port: number }).port });
    });
  });
}

type FgWireEvent = { seq: number; type: string; public_payload: unknown;
  context_reference?: { build_id: number; attempt: number; used_handles: string[] } };

function resetState(): void {
  state.jobs.length = 0;
  state.toolCalls.length = 0;
  state.settles.length = 0;
  state.cancelOnNextHeartbeat = false;
  state.heartbeatStatus = undefined;
  state.eventsByRun.clear();
}

/** Enqueue one assistant job; returns the run key used in assertions. */
function enqueueJob(options: {
  allowlist: string[];
  provider?: Record<string, unknown>;
  agentKind?: "assistant" | "unexpected";
  messages?: RunContextMessage[];
  contextBlocks?: RunContextBlock[];
  contextBuildId?: number;
}): string {
  idCounter += 1;
  const jobId = 4000 + idCounter;
  const runId = 5000 + idCounter;
  state.jobs.push({
    job_id: jobId,
    run_id: runId,
    agent_kind: options.agentKind ?? "assistant",
    attempt: 0,
    tool_allowlist: [...options.allowlist],
    policy_version: "pv-it-1",
    run_token: `run-token-${runId}`,
    allowlist: [...options.allowlist],
    ...(options.provider !== undefined ? { provider: options.provider } : {}),
    ...(options.messages !== undefined ? { messages: options.messages } : {}),
    ...(options.contextBlocks !== undefined ? { contextBlocks: options.contextBlocks } : {}),
    contextBuildId: options.contextBuildId,
  });
  // Backend enqueue owns message.user_added at seq 0 (interactive assistant run).
  state.eventsByRun.set(String(runId), [
    {
      seq: 0,
      type: "message.user_added",
      public_payload: { role: "user", text: CURRENT_PROMPT },
    },
  ]);
  return String(runId);
}

function contextProjection(job: MockJob): Record<string, unknown> {
  return {
    run_id: job.run_id,
    session_id: 700,
    agent_kind: job.agent_kind,
    account_id: 900,
    space_id: 800,
    status: "leased",
    attempt: job.attempt,
    policy_version: job.policy_version,
    tool_allowlist: [...job.allowlist],
    messages: job.messages ?? [
      {
        id: 11,
        role: "user",
        content_json: { text: CURRENT_PROMPT },
        created_at: new Date().toISOString(),
      },
    ],
    ...(job.contextBlocks !== undefined ? { context_blocks: job.contextBlocks } : {}),
    context_build_id: job.contextBuildId ?? null,
    provider:
      job.provider ??
      ({
        provider_id: 3,
        model: "test-model",
        kind: "openai_compatible",
        api: "openai-completions",
        compat: { maxTokensField: "max_tokens" },
        context_window: 272000,
        max_tokens: 60000,
        reasoning: true,
        input_modalities: ["text", "image"],
        thinking_levels: ["low", "medium", "high", "xhigh", "max"],
        policy_result: "allowed",
        secret_ref: "agent_providers/3",
        base_url: `/internal/agent/runs/${job.run_id}/provider`,
        api_key: null,
      } satisfies Record<string, unknown>),
    cancel_requested: false,
    next_event_seq: 1,
  };
}

// ---------------------------------------------------------------------------
// Scripted offline provider stream
// ---------------------------------------------------------------------------

const usage = {
  input: 10,
  output: 5,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 15,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

function assistantMessage(partial: Omit<AssistantMessage, "role">): AssistantMessage {
  return { role: "assistant", ...partial };
}

interface ScriptOptions {
  leakSecretInPayload?: boolean;
  /** Captures the post-onPayload request payloads (what would hit the wire). */
  wirePayloads?: unknown[];
  /** Snapshot the real Pi context before the scripted response. */
  modelContexts?: Context[];
  sessionEvents?: AgentSessionEvent[];
  responseFor?: (context: Context) => AssistantMessage;
  /** Keep the provider stream open until the worker's AbortSignal fires. */
  waitForAbort?: boolean;
  /** Simulate a provider completing successfully while cancellation is in flight. */
  completeAfterAbort?: boolean;
}

function scriptedStream(
  turns: AssistantMessage[][],
  options: ScriptOptions = {},
): (
  model: Model<"openai-completions">,
  context: Context,
  streamOptions?: SimpleStreamOptions,
) => AssistantMessageEventStream {
  return (_model, context, streamOptions) => {
    options.modelContexts?.push({ ...context, messages: structuredClone(context.messages) });
    const stream = createAssistantMessageEventStream();
    void (async () => {
      // Assemble a provider-shaped request payload; invoking onPayload runs
      // the sidecar's before_provider_request hook (secret scan).
      const payload = {
        model: _model.id,
        messages: context.messages.map((m) => ({ role: m.role, content: "content" })),
        // The sidecar must never send its service credential in a provider body.
        // Provider API keys are no longer present in sidecar config; the
        // service secret is the real secret available to this runtime.
        ...(options.leakSecretInPayload ? { api_key: "integration-service-secret" } : {}),
      };
      let finalPayload: unknown = payload;
      try {
        if (streamOptions?.onPayload) {
          finalPayload = await streamOptions.onPayload(payload, _model);
        }
      } catch {
        // A policy rejection must stop this provider call before it reaches
        // the wire; finish the scripted stream so the worker can settle it.
        const blocked = turns[0]![0]!;
        const failed = { ...blocked, stopReason: "error" as const, errorMessage: "policy blocked" };
        stream.push({ type: "start", partial: failed });
        stream.push({ type: "error", reason: "error", error: failed });
        stream.end(failed);
        return;
      }
      options.wirePayloads?.push(finalPayload);

      if (options.waitForAbort) {
        await new Promise<void>((resolve) => {
          if (streamOptions?.signal?.aborted) return resolve();
          streamOptions?.signal?.addEventListener("abort", () => resolve(), { once: true });
        });
        if (!options.completeAfterAbort) {
          const blocked = turns[0]![0]!;
          const failed = { ...blocked, stopReason: "aborted" as const, errorMessage: "aborted" };
          stream.push({ type: "start", partial: failed });
          stream.push({ type: "error", reason: "aborted", error: failed });
          stream.end(failed);
          return;
        }
      }

      const turnIndex = context.messages.filter((m) => m.role === "toolResult").length;
      const message: AssistantMessage = {
        ...(options.responseFor?.(context) ?? turns[Math.min(turnIndex, turns.length - 1)]![0]!),
        api: _model.api,
        provider: _model.provider,
        model: _model.id,
        timestamp: Date.now(),
      };
      stream.push({ type: "start", partial: message });
      for (const [index, block] of message.content.entries()) {
        if (block.type === "text") {
          stream.push({ type: "text_start", contentIndex: index, partial: message });
          stream.push({
            type: "text_delta",
            contentIndex: index,
            delta: block.text,
            partial: message,
          });
          stream.push({
            type: "text_end",
            contentIndex: index,
            content: block.text,
            partial: message,
          });
        } else if (block.type === "toolCall") {
          stream.push({ type: "toolcall_start", contentIndex: index, partial: message });
          stream.push({
            type: "toolcall_end",
            contentIndex: index,
            toolCall: block,
            partial: message,
          });
        }
      }
      if (message.stopReason === "error") {
        stream.push({ type: "error", reason: "error", error: message });
      } else {
        stream.push({
          type: "done",
          reason: message.stopReason === "toolUse" ? "toolUse" : "stop",
          message,
        });
      }
      stream.end(message);
    })();
    return stream;
  };
}

function echoToolCallTurn(callId: string, args: Record<string, unknown>): AssistantMessage[] {
  return [
    assistantMessage({
      content: [
        {
          type: "toolCall",
          id: callId,
          name: providerWireName("familygraph.echo"),
          arguments: args,
        },
      ],
      api: "openai-completions",
      provider: "cloud",
      model: "test-model",
      usage,
      stopReason: "toolUse",
      timestamp: Date.now(),
    }),
  ];
}

function listVisiblePeopleToolCallTurn(
  callId: string,
  args: Record<string, unknown>,
): AssistantMessage[] {
  return [
    assistantMessage({
      content: [
        {
          type: "toolCall",
          id: callId,
          name: providerWireName("familygraph.list_visible_people"),
          arguments: args,
        },
      ],
      api: "openai-completions",
      provider: "cloud",
      model: "test-model",
      usage,
      stopReason: "toolUse",
      timestamp: Date.now(),
    }),
  ];
}

function textTurn(text: string): AssistantMessage[] {
  return [
    assistantMessage({
      content: [{ type: "text", text }],
      api: "openai-completions",
      provider: "cloud",
      model: "test-model",
      usage,
      stopReason: "stop",
      timestamp: Date.now(),
    }),
  ];
}

function overflowRecoveryResponse(retryError?: string): (context: Context) => AssistantMessage {
  let normalRequests = 0;
  return (context) => {
    const input = context.messages
      .map((message) =>
        typeof message.content === "string"
          ? message.content
          : message.content
              .filter((block) => block.type === "text")
              .map((block) => block.text)
              .join(""),
      )
      .join("\n");
    if (context.systemPrompt?.startsWith("You are a context summarization assistant.")) {
      // Derive the checkpoint from the actual summary request, not a canned answer.
      const fact = input.match(/The blue tin is kept in the [^.\n]+\./)?.[0];
      return textTurn("Recovered checkpoint: " + (fact ?? "No fact in summary input."))[0]!;
    }
    const errorMessage = ++normalRequests === 1 ? "maximum context length exceeded" : retryError;
    if (errorMessage) {
      return { ...textTurn("")[0]!, stopReason: "error", errorMessage };
    }
    const fact = input.match(/Recovered checkpoint: (The blue tin is kept in the [^.\n]+\.)/)?.[1];
    return textTurn("Recovered answer: " + (fact ?? "No checkpoint in retry input."))[0]!;
  };
}

async function buildSessionFactory(
  turns: AssistantMessage[][],
  options: ScriptOptions = {},
): Promise<NonNullable<ConstructorParameters<typeof SidecarWorker>[0]["sessionFactory"]>> {
  const mod = await import("../src/session.js");
  return async (cfg, cl, projection, runToken, deps) => {
    const bundle = await mod.buildRunSession(cfg, cl, projection, runToken, {
      ...deps,
      streamOverride: scriptedStream(turns, options),
    });
    if (options.sessionEvents)
      bundle.session.subscribe((event) => options.sessionEvents!.push(event));
    return bundle;
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("worker full cycle against mock FastAPI", () => {
  let port = 0;
  let server: Server;

  beforeAll(async () => {
    ({ server, port } = await startMockFastAPI());
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  function makeWorker(
    configOverrides?: (config: AgentConfig) => void,
    sessionFactory?: ReturnType<typeof buildSessionFactory> extends Promise<infer F> ? F : never,
  ): { worker: SidecarWorker; client: InternalClient } {
    const config = makeConfig(port);
    configOverrides?.(config);
    const client = new InternalClient(config);
    const worker = new SidecarWorker({ client, config, logger: createLogger(), sessionFactory });
    return { worker, client };
  }

  it("leases → context → Pi session → echo tool via FastAPI → events → settle succeeded", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo", "familygraph.probe_scope"],
    });
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([echoToolCallTurn("tc_1", { text: "ping" }), textTurn("done")]),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // Tool executed exactly once through FastAPI with the authoritative body.
    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0]).toMatchObject({ run_id: runKey, tool: "familygraph.echo" });
    const envelope = state.toolCalls[0]!.body;
    expect(envelope["version"]).toBe(1);
    expect(envelope["tool_call_id"]).toBe("tc_1");
    expect(envelope["input"]).toEqual({ text: "ping" });
    expect("tool_version" in envelope).toBe(false);

    // Settle succeeded exactly once, posted to /settle without error fields.
    expect(state.settles).toEqual([
      { run_id: runKey, status: "succeeded", error_code: undefined, error: undefined },
    ]);

    // Event stream: monotonic seq, lifecycle order, whitelisted payloads.
    const events = state.eventsByRun.get(runKey) ?? [];
    const types = events.map((e) => e.type);
    // message.user_added is backend-owned (seq 0); the sidecar did not re-emit it.
    expect(types.filter((t) => t === "message.user_added")).toHaveLength(1);
    expect(types[0]).toBe("message.user_added");
    expect(types.indexOf("run.started")).toBe(1);
    expect(types.filter((t) => t === "turn.completed").length).toBeGreaterThanOrEqual(2);
    expect(types).toContain("tool.execution.started");
    expect(types).toContain("tool.execution.completed");
    expect(types).toContain("message.assistant_added");
    // Terminal event is backend-owned (written by /settle), exactly once.
    expect(types.filter((t) => t === "run.settled").length).toBe(1);
    expect(types[types.length - 1]).toBe("run.settled");
    const seqs = events.map((e) => e.seq);
    expect([...seqs].sort((a, b) => a - b)).toEqual(seqs);
    // Tool completion carries only ids + error flag, no raw result.
    const completed = events.find((e) => e.type === "tool.execution.completed");
    expect(completed?.public_payload).toEqual({
      tool_call_id: "tc_1",
      tool_name: "familygraph.echo",
      is_error: false,
    });
    // Assistant texts projected without provider metadata. Turn 1 carries only
    // a tool call (no prose), so it produces no assistant event at all.
    const assistants = events.filter((e) => e.type === "message.assistant_added");
    expect(assistants.map((e) => (e.public_payload as { text: string }).text)).toEqual(["done"]);
    expect(JSON.stringify(events)).not.toContain("openai-completions");
  }, 30000);

  it("rejects a malformed non-assistant lease response at the sidecar boundary", async () => {
    resetState();
    enqueueJob({
      allowlist: ["familygraph.echo"],
      agentKind: "unexpected",
    });
    const client = new InternalClient(makeConfig(port));
    await expect(client.leaseJob()).rejects.toThrow("non-assistant");
  });

  it("restores history and sends the current user and RAG block once without replaying events", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
      messages: historyMessages(),
      contextBlocks: [
        {
          source_id: "current-source",
          source_type: "memory",
          scope: "private",
          sensitivity: "normal",
          revision: 1,
          citation: "[R1]",
          content: "current-authorized-rag-data",
        },
      ],
    });
    const modelContexts: Context[] = [];
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([textTurn("done")], { modelContexts }),
    );
    expect(await worker.tryLeaseAndRun()).toBe(true);
    expect(modelContexts).toHaveLength(1);
    expect(modelContexts[0]!.messages.map((message) => message.role)).toEqual([
      "user",
      "assistant",
      "user",
    ]);
    const requestText = JSON.stringify(modelContexts[0]!.messages);
    expect(requestText).toContain(HISTORY_FACT);
    expect(requestText).toContain("Acknowledged.");
    expect(requestText.split(CURRENT_PROMPT)).toHaveLength(2);
    expect(requestText.split("current-authorized-rag-data")).toHaveLength(2);
    expect(requestText).not.toMatch(/old-(private|tool|rag)/);
    expect(state.toolCalls).toHaveLength(0);
    const events = state.eventsByRun.get(runKey) ?? [];
    expect(events.filter((event) => event.type === "message.user_added")).toHaveLength(1);
    expect(
      events
        .filter((event) => event.type === "message.assistant_added")
        .map((event) => event.public_payload),
    ).toEqual([{ role: "assistant", text: "done" }]);
    expect(state.settles[0]).toMatchObject({ run_id: runKey, status: "succeeded" });
  });

  it("sends the server build and actual completed citation over the InternalClient wire", async () => {
    resetState();
    const handle = "rag:42:r1:c9";
    const unused = "rag:43:r1:c10";
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"], contextBuildId: 471, contextBlocks: [
      { source_id: "42", source_type: "memory", scope: "private", sensitivity: "normal",
        revision: 1, citation: handle, content: "synthetic relevant material" },
      { source_id: "43", source_type: "memory", scope: "private", sensitivity: "normal",
        revision: 1, citation: unused, content: "synthetic unused material" },
    ] });
    const modelContexts: Context[] = [];
    const { worker } = makeWorker(undefined, await buildSessionFactory([
      textTurn(`The answer [${handle}]`),
    ], { modelContexts }));
    expect(await worker.tryLeaseAndRun()).toBe(true);
    expect(JSON.stringify(modelContexts)).toContain(unused);
    const answer = state.eventsByRun.get(runKey)?.find((e) => e.type === "message.assistant_added");
    expect(answer?.context_reference).toEqual({ build_id: 471, attempt: 1, used_handles: [handle] });
    expect(answer?.public_payload).toEqual({ role: "assistant", text: `The answer [${handle}]` });
    expect(state.settles[0]).toMatchObject({ run_id: runKey, status: "succeeded" });
  });

  it.each([
    { result: "succeeded", retryError: undefined },
    { result: "failed", retryError: "retry request rejected" },
  ])(
    "settles $result from the final reply after real overflow compaction and retry",
    async ({ retryError }) => {
      resetState();
      const runKey = enqueueJob({
        allowlist: ["familygraph.echo"],
        messages: compactionHistoryMessages(),
      });
      const modelContexts: Context[] = [];
      const sessionEvents: AgentSessionEvent[] = [];
      const { worker } = makeWorker(
        undefined,
        await buildSessionFactory([textTurn("")], {
          modelContexts,
          sessionEvents,
          responseFor: overflowRecoveryResponse(retryError),
        }),
      );

      expect(await worker.tryLeaseAndRun()).toBe(true);
      expect(
        modelContexts.map((context) =>
          context.systemPrompt?.startsWith("You are a context summarization assistant."),
        ),
      ).toEqual([false, true, false]);
      expect(sessionEvents).toContainEqual(
        expect.objectContaining({
          type: "compaction_end",
          reason: "overflow",
          aborted: false,
          willRetry: true,
          result: expect.objectContaining({ summary: "Recovered checkpoint: " + HISTORY_FACT }),
        }),
      );
      expect(JSON.stringify(modelContexts[2]!.messages)).toContain(
        "Recovered checkpoint: " + HISTORY_FACT,
      );
      const finalMessage = sessionEvents
        .filter((event) => event.type === "message_end" && event.message.role === "assistant")
        .at(-1);
      expect(finalMessage).toMatchObject({
        message: { stopReason: retryError ? "error" : "stop" },
      });
      if (retryError) {
        expect(state.settles).toEqual([
          {
            run_id: runKey,
            status: "failed",
            error_code: "PROVIDER_STREAM_ERROR",
            error: { message: retryError },
          },
        ]);
      } else {
        const assistants = (state.eventsByRun.get(runKey) ?? []).filter(
          (event) => event.type === "message.assistant_added",
        );
        expect(assistants.at(-1)?.public_payload).toEqual({
          role: "assistant",
          text: "Recovered answer: " + HISTORY_FACT,
        });
        expect(state.settles).toEqual([
          {
            run_id: runKey,
            status: "succeeded",
            error_code: undefined,
            error: undefined,
          },
        ]);
      }
    },
  );

  it("settles a persistent context overflow as a provider failure with restored history intact", async () => {
    resetState();
    const messages = historyMessages();
    const current = { ...messages[2]!, id: 5 };
    const recentText = "Recent synthetic note. ".repeat(4_000);
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
      messages: [
        ...messages.slice(0, 2),
        {
          id: 3,
          role: "user",
          content_json: { text: recentText },
          created_at: "2026-09-01T00:00:02Z",
        },
        {
          id: 4,
          role: "assistant",
          content_json: { text: "Ready." },
          created_at: "2026-09-01T00:00:03Z",
        },
        current,
      ],
    });
    const failed: AssistantMessage = {
      ...textTurn("")[0]!,
      stopReason: "error",
      errorMessage: "maximum context length exceeded",
    };
    const modelContexts: Context[] = [];
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([[failed]], { modelContexts }),
    );
    expect(await worker.tryLeaseAndRun()).toBe(true);
    expect(modelContexts).toHaveLength(2);
    expect(JSON.stringify(modelContexts[0]!.messages)).toContain(HISTORY_FACT);
    expect(JSON.stringify(modelContexts[0]!.messages)).toContain(recentText);
    expect(modelContexts[1]!.systemPrompt).toContain("context summarization assistant");
    expect(JSON.stringify(modelContexts[1]!.messages)).toContain(HISTORY_FACT);
    expect(state.settles).toEqual([
      {
        run_id: runKey,
        status: "failed",
        error_code: "PROVIDER_STREAM_ERROR",
        error: { message: "maximum context length exceeded" },
      },
    ]);
    expect(
      (state.eventsByRun.get(runKey) ?? []).filter((event) => event.type === "run.settled"),
    ).toHaveLength(0);
  });

  it("blocks non-allowlisted tools without touching execute endpoint", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    const blockedToolTurn: AssistantMessage[] = [
      assistantMessage({
        content: [
          {
            type: "toolCall",
            id: "tc_evil",
            name: "familygraph.read_file",
            arguments: { path: "/etc/passwd" },
          },
        ],
        api: "openai-completions",
        provider: "cloud",
        model: "test-model",
        usage,
        stopReason: "toolUse",
        timestamp: Date.now(),
      }),
    ];
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([blockedToolTurn, textTurn("ok")]),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // The disallowed tool never reached the execute endpoint...
    expect(state.toolCalls).toHaveLength(0);
    // ...and the run settled failed with the policy code (audit trail).
    expect(state.settles).toHaveLength(1);
    expect(state.settles[0]!.status).toBe("failed");
    expect(state.settles[0]!.error_code).toBe("POLICY_TOOL_BLOCKED");
    const events = state.eventsByRun.get(runKey) ?? [];
    expect(events.some((e) => e.type === "run.failed")).toBe(true);
    expect(
      events.filter((e) => e.type === "message.assistant_added").at(-1)?.public_payload,
    ).toEqual({ role: "assistant", text: "ok" });
    expect(JSON.stringify(events)).not.toContain("/etc/passwd");
  }, 30000);

  it("redacts secrets in provider payloads and fails the run", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    const wirePayloads: unknown[] = [];
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([textTurn("hello")], {
        leakSecretInPayload: true,
        wirePayloads,
      }),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // Secret never appears unredacted in wire-bound payloads or persisted events.
    expect(JSON.stringify(wirePayloads)).not.toContain("integration-service-secret");
    expect(JSON.stringify(state.eventsByRun.get(runKey) ?? [])).not.toContain(
      "integration-service-secret",
    );
    expect(state.settles).toHaveLength(1);
    expect(state.settles[0]!.status).toBe("failed");
    expect(state.settles[0]!.error_code).toBe("POLICY_SECRET_LEAK");
  }, 30000);

  it("refuses explainably when provider policy_result is not allowed (no model loop)", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
      messages: historyMessages(),
      provider: {
        provider_id: 9,
        model: null,
        kind: "local",
        policy_result: "denied_no_local" satisfies ProviderPolicyResult,
        secret_ref: null,
      },
    });
    let modelLoopStarted = false;
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([textTurn("must never stream")]).then((factory) => {
        return (cfg, cl, projection, runToken, deps) => {
          modelLoopStarted = true;
          return factory(cfg, cl, projection, runToken, deps);
        };
      }),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // Explainable refusal: no model loop, no tool call, no sidecar-re-emitted user event.
    expect(modelLoopStarted).toBe(false);
    expect(state.toolCalls).toHaveLength(0);
    expect(state.settles).toHaveLength(1);
    expect(state.settles[0]).toMatchObject({
      run_id: runKey,
      status: "failed",
      error_code: "PROVIDER_DENIED_NO_LOCAL",
    });
    expect(state.settles[0]!.error).toEqual({
      message: "provider policy refuses this run (denied_no_local)",
    });
    const events = state.eventsByRun.get(runKey) ?? [];
    // message.user_added (seq 0, backend-owned) is the only non-terminal event;
    // run.failed is written by the backend /settle handler.
    expect(events.map((e) => e.type)).toEqual(["message.user_added", "run.failed"]);
    expect(events[1]!.public_payload).toMatchObject({ error_code: "PROVIDER_DENIED_NO_LOCAL" });
  }, 30000);

  it.each([false, true])(
    "propagates server cancellation and skips settle (late success=%s)",
    async (completeAfterAbort) => {
      resetState();
      enqueueJob({ allowlist: ["familygraph.echo"], messages: historyMessages() });
      state.cancelOnNextHeartbeat = true;
      const sessionEvents: AgentSessionEvent[] = [];
      const { worker } = makeWorker(
        (cfg) => {
          // Heartbeat cadence is clamped to 1s; keep the scripted stream open
          // long enough for the cancel flag to reach the worker.
          cfg.defaultLeaseMs = 3_000;
        },
        await buildSessionFactory([textTurn("never committed")], {
          waitForAbort: true,
          completeAfterAbort,
          sessionEvents,
        }),
      );

      expect(await worker.tryLeaseAndRun()).toBe(true);
      expect(state.settles).toHaveLength(0);
      if (completeAfterAbort) {
        expect(sessionEvents).toContainEqual(
          expect.objectContaining({
            type: "message_end",
            message: expect.objectContaining({ role: "assistant", stopReason: "stop" }),
          }),
        );
      }
    },
    10000,
  );

  it.each([false, true])(
    "skips settle after heartbeat authorization is revoked (late success=%s)",
    async (completeAfterAbort) => {
      resetState();
      enqueueJob({ allowlist: ["familygraph.echo"], messages: historyMessages() });
      state.heartbeatStatus = 403;
      const sessionEvents: AgentSessionEvent[] = [];
      const { worker } = makeWorker(
        (cfg) => {
          cfg.defaultLeaseMs = 3_000;
        },
        await buildSessionFactory([textTurn("never committed")], {
          waitForAbort: true,
          completeAfterAbort,
          sessionEvents,
        }),
      );

      expect(await worker.tryLeaseAndRun()).toBe(true);
      expect(state.settles).toHaveLength(0);
      if (completeAfterAbort) {
        expect(sessionEvents).toContainEqual(
          expect.objectContaining({
            type: "message_end",
            message: expect.objectContaining({ role: "assistant", stopReason: "stop" }),
          }),
        );
      }
    },
    10000,
  );

  it("settles failed with PROVIDER_EMPTY_ANSWER when the model returns no answer text", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    const { worker } = makeWorker(undefined, await buildSessionFactory([textTurn("")]));

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // A run without an answer is a failure, not a silent success.
    expect(state.settles).toEqual([
      {
        run_id: runKey,
        status: "failed",
        error_code: "PROVIDER_EMPTY_ANSWER",
        error: { message: "model completed the run without returning any answer text" },
      },
    ]);
    // No blank assistant row is persisted (it would replay as empty history).
    const events = state.eventsByRun.get(runKey) ?? [];
    expect(events.filter((e) => e.type === "message.assistant_added")).toHaveLength(0);
    const terminal = events[events.length - 1]!;
    expect(terminal.type).toBe("run.failed");
    expect(terminal.public_payload).toMatchObject({ error_code: "PROVIDER_EMPTY_ANSWER" });
  }, 30000);

  it("does not accept tool-turn prose as the final answer", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    const proseToolTurn: AssistantMessage[] = [
      assistantMessage({
        content: [
          { type: "text", text: "let me check" },
          {
            type: "toolCall",
            id: "tc_prose",
            name: providerWireName("familygraph.echo"),
            arguments: { text: "ping" },
          },
        ],
        api: "openai-completions",
        provider: "cloud",
        model: "test-model",
        usage,
        stopReason: "toolUse",
        timestamp: Date.now(),
      }),
    ];
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([proseToolTurn, textTurn("")]),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // The prose tool turn is reported, but the empty stop message that follows
    // it is the final answer, and it carries nothing.
    expect(state.toolCalls).toHaveLength(1);
    const events = state.eventsByRun.get(runKey) ?? [];
    expect(
      events
        .filter((e) => e.type === "message.assistant_added")
        .map((e) => (e.public_payload as { text: string }).text),
    ).toEqual(["let me check"]);
    expect(state.settles).toEqual([
      {
        run_id: runKey,
        status: "failed",
        error_code: "PROVIDER_EMPTY_ANSWER",
        error: { message: "model completed the run without returning any answer text" },
      },
    ]);
  }, 30000);

  it("keeps settling succeeded when the truncated final answer still has text", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    const truncated: AssistantMessage = { ...textTurn("partial answer")[0]!, stopReason: "length" };
    const { worker } = makeWorker(undefined, await buildSessionFactory([[truncated]]));

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // Truncation is a partial answer, not an empty one: no regression.
    expect(state.settles).toEqual([
      { run_id: runKey, status: "succeeded", error_code: undefined, error: undefined },
    ]);
  }, 30000);

  it("treats the LAST completed answer as final: truncated prose then empty retry fails", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
      messages: compactionHistoryMessages(),
    });
    let normalCalls = 0;
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([textTurn("unused")], {
        responseFor: (context) => {
          if (context.systemPrompt?.startsWith("You are a context summarization assistant.")) {
            return textTurn("checkpoint")[0]!;
          }
          normalCalls += 1;
          // First completed answer: truncated, but it carries prose. The
          // overflow recovery then retries and that retry answers with nothing.
          return normalCalls === 1
            ? { ...textTurn("truncated partial prose")[0]!, stopReason: "length" }
            : textTurn("")[0]!;
        },
      }),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // The truncated turn is a real (partial) answer and stays reported...
    expect(normalCalls).toBe(2);
    const assistants = (state.eventsByRun.get(runKey) ?? [])
      .filter((event) => event.type === "message.assistant_added")
      .map((event) => (event.public_payload as { text: string }).text);
    expect(assistants).toEqual(["truncated partial prose"]);
    // ...but the LAST completed answer is the empty retry, so the run has no
    // answer to show: tracking the first message instead would report success.
    expect(state.settles).toEqual([
      {
        run_id: runKey,
        status: "failed",
        error_code: "PROVIDER_EMPTY_ANSWER",
        error: { message: "model completed the run without returning any answer text" },
      },
    ]);
  }, 30000);

  it("fails when no completed answer exists at all (turn ended aborted without a sidecar abort)", async () => {
    resetState();
    const runKey = enqueueJob({ allowlist: ["familygraph.echo"] });
    // Pi can end the loop with a message that is neither stop nor length while
    // the sidecar never initiated an abort (e.g. the server-side cancellation
    // was not observed). No final answer was produced, so success is a lie.
    const aborted: AssistantMessage[] = [
      { ...textTurn("")[0]!, stopReason: "aborted", errorMessage: "aborted" },
    ];
    const { worker } = makeWorker(undefined, await buildSessionFactory([aborted]));

    expect(await worker.tryLeaseAndRun()).toBe(true);

    expect(state.settles).toEqual([
      {
        run_id: runKey,
        status: "failed",
        error_code: "PROVIDER_EMPTY_ANSWER",
        error: { message: "model completed the run without returning any answer text" },
      },
    ]);
  }, 30000);

  it("returns false when queue is empty (HTTP 204)", async () => {
    resetState();
    const { worker } = makeWorker();
    expect(await worker.tryLeaseAndRun()).toBe(false);
    expect(state.settles).toHaveLength(0);
  });

  it("V2.2: assistant run drives familygraph.list_visible_people end-to-end via FastAPI", async () => {
    resetState();
    // Full six-tool assistant allowlist — session construction only succeeds
    // because every name is declared in the sidecar registry.
    const runKey = enqueueJob({
      allowlist: [
        "familygraph.get_self_context",
        "familygraph.list_visible_people",
        "familygraph.get_profile_summary",
        "familygraph.search_space",
        "familygraph.get_relationship_path",
        "familygraph.explain_structural_path",
      ],
    });
    const { worker } = makeWorker(
      undefined,
      await buildSessionFactory([
        listVisiblePeopleToolCallTurn("tc_lv1", { query: "王", limit: 5 }),
        textTurn("在当前空间找到 1 位可见家人：王明。"),
      ]),
    );

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // Execute reached FastAPI exactly once with the shared-contract body.
    expect(state.toolCalls).toHaveLength(1);
    expect(state.toolCalls[0]).toMatchObject({
      run_id: runKey,
      tool: "familygraph.list_visible_people",
    });
    expect(state.toolCalls[0]!.body).toEqual({
      version: 1,
      input: { query: "王", limit: 5 },
      tool_call_id: "tc_lv1",
    });

    // Event sequence keeps the C3-rendered payload shapes.
    const events = state.eventsByRun.get(runKey) ?? [];
    const started = events.find((e) => e.type === "tool.execution.started");
    expect(started?.public_payload).toEqual({
      tool_call_id: "tc_lv1",
      tool_name: "familygraph.list_visible_people",
      tool_version: 1,
    });
    const completed = events.find((e) => e.type === "tool.execution.completed");
    expect(completed?.public_payload).toEqual({
      tool_call_id: "tc_lv1",
      tool_name: "familygraph.list_visible_people",
      is_error: false,
    });
    expect(events[events.length - 1]!.type).toBe("run.settled");
    expect(state.settles).toEqual([
      { run_id: runKey, status: "succeeded", error_code: undefined, error: undefined },
    ]);
  }, 30000);
});
