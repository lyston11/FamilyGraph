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
import {
  createAssistantMessageEventStream,
  type AssistantMessage,
  type AssistantMessageEventStream,
  type Context,
  type Model,
  type SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import { InternalClient, type ProviderPolicyResult } from "../src/client.js";
import type { AgentConfig } from "../src/config.js";
import { createLogger } from "../src/logger.js";
import { SidecarWorker } from "../src/worker.js";

// ---------------------------------------------------------------------------
// Shared mock state + mock FastAPI (authoritative backend shapes)
// ---------------------------------------------------------------------------

interface MockJob {
  job_id: number;
  run_id: number;
  agent_kind: "assistant" | "steward";
  /** Attempt counter, incremented by the mock at each lease like the backend. */
  attempt: number;
  tool_allowlist: string[];
  policy_version: string;
  run_token: string;
  allowlist: string[];
  leasedAt?: number;
  /** Override for the context projection's provider resolution. */
  provider?: Record<string, unknown>;
}

interface MockState {
  jobs: MockJob[];
  /** Keyed by String(run_id). */
  eventsByRun: Map<string, Array<{ seq: number; type: string; public_payload: unknown }>>;
  toolCalls: Array<{ run_id: string; tool: string; body: Record<string, unknown> }>;
  settles: Array<{
    run_id: string;
    status: string;
    error_code?: unknown;
    error?: unknown;
  }>;
}

const state: MockState = {
  jobs: [],
  eventsByRun: new Map(),
  toolCalls: [],
  settles: [],
};

let idCounter = 0;

function makeConfig(port: number): AgentConfig {
  return {
    apiBaseUrl: `http://127.0.0.1:${port}`,
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
        // Strict schema: only {leased_by} may be sent (extra=forbid).
        if (!("leased_by" in body) || "kind" in body || "sidecar_id" in body) {
          return respond(422, { detail: "lease body must be {leased_by}" });
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
        const body = await readBody(req);
        if (Object.keys(body).length > 0) return respond(422, { detail: "heartbeat body is {}" });
        return respond(200, {
          ok: true,
          lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
          cancel_requested: false,
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

type FgWireEvent = { seq: number; type: string; public_payload: unknown };

function resetState(): void {
  state.jobs.length = 0;
  state.toolCalls.length = 0;
  state.settles.length = 0;
  state.eventsByRun.clear();
}

/** Enqueue one assistant job; returns the run key used in assertions. */
function enqueueJob(
  options: {
    allowlist: string[];
    provider?: Record<string, unknown>;
    agentKind?: "assistant" | "steward";
  },
): string {
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
  });
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
    messages: [
      {
        id: 11,
        role: "user",
        content_json: { text: "Please echo 'ping' then say done." },
        created_at: new Date().toISOString(),
      },
    ],
    provider:
      job.provider ??
      ({
        provider_id: 3,
        model: "test-model",
        kind: "openai_compatible",
        policy_result: "allowed",
        secret_ref: null,
      } satisfies Record<string, unknown>),
    cancel_requested: false,
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
    const stream = createAssistantMessageEventStream();
    void (async () => {
      // Assemble a provider-shaped request payload; invoking onPayload runs
      // the sidecar's before_provider_request hook (secret scan).
      const payload = {
        model: _model.id,
        messages: context.messages.map((m) => ({ role: m.role, content: "content" })),
        ...(options.leakSecretInPayload ? { api_key: "sk-integration-cloud-key" } : {}),
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
        stream.push({ type: "start", partial: blocked });
        stream.push({ type: "done", reason: "stop", message: blocked });
        stream.end(blocked);
        return;
      }
      options.wirePayloads?.push(finalPayload);

      const turnIndex = context.messages.filter((m) => m.role === "toolResult").length;
      const message = turns[Math.min(turnIndex, turns.length - 1)]![0]!;
      stream.push({ type: "start", partial: message });
      for (const [index, block] of message.content.entries()) {
        if (block.type === "text") {
          stream.push({ type: "text_start", contentIndex: index, partial: message });
          stream.push({ type: "text_delta", contentIndex: index, delta: block.text, partial: message });
          stream.push({ type: "text_end", contentIndex: index, content: block.text, partial: message });
        } else if (block.type === "toolCall") {
          stream.push({ type: "toolcall_start", contentIndex: index, partial: message });
          stream.push({ type: "toolcall_end", contentIndex: index, toolCall: block, partial: message });
        }
      }
      stream.push({
        type: "done",
        reason: message.stopReason === "toolUse" ? "toolUse" : "stop",
        message,
      });
      stream.end(message);
    })();
    return stream;
  };
}

function echoToolCallTurn(callId: string, args: Record<string, unknown>): AssistantMessage[] {
  return [
    assistantMessage({
      content: [{ type: "toolCall", id: callId, name: "familygraph.echo", arguments: args }],
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
        { type: "toolCall", id: callId, name: "familygraph.list_visible_people", arguments: args },
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

async function buildSessionFactory(
  turns: AssistantMessage[][],
  options: ScriptOptions = {},
): Promise<
  NonNullable<ConstructorParameters<typeof SidecarWorker>[0]["sessionFactory"]>
> {
  const mod = await import("../src/session.js");
  return (cfg, cl, projection, runToken, deps) =>
    mod.buildRunSession(cfg, cl, projection, runToken, {
      ...deps,
      streamOverride: scriptedStream(turns, options),
    });
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
    expect(types.indexOf("message.user_added")).toBeGreaterThanOrEqual(0);
    expect(types.indexOf("message.user_added")).toBeLessThan(types.indexOf("run.started"));
    expect(types.filter((t) => t === "turn.completed").length).toBeGreaterThanOrEqual(2);
    expect(types).toContain("tool.execution.started");
    expect(types).toContain("tool.execution.completed");
    expect(types).toContain("message.assistant_added");
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
    // Assistant texts projected without provider metadata (one per turn).
    const assistants = events.filter((e) => e.type === "message.assistant_added");
    // Turn 1 carries only a tool call (no text), turn 2 the final text.
    expect(assistants.map((e) => (e.public_payload as { text: string }).text)).toEqual([
      "",
      "done",
    ]);
    expect(JSON.stringify(events)).not.toContain("openai-completions");
  }, 30000);

  it("consumes the real-shape lease response (attempt/tool_allowlist/policy_version)", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
      agentKind: "steward",
    });
    const client = new InternalClient(makeConfig(port));
    const job = await client.leaseJob();
    expect(job).toEqual({
      job_id: String(Number(runKey) - 1000),
      run_id: runKey,
      agent_kind: "steward",
      attempt: 1,
      tool_allowlist: ["familygraph.echo"],
      policy_version: "pv-it-1",
      run_token: `run-token-${runKey}`,
    });
    // No lease_ms is advertised; heartbeat derives from config.defaultLeaseMs.
    expect("lease_ms" in (job as unknown as Record<string, unknown>)).toBe(false);
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
    const { worker } = makeWorker(undefined, await buildSessionFactory([blockedToolTurn, textTurn("ok")]));

    expect(await worker.tryLeaseAndRun()).toBe(true);

    // The disallowed tool never reached the execute endpoint...
    expect(state.toolCalls).toHaveLength(0);
    // ...and the run settled failed with the policy code (audit trail).
    expect(state.settles).toHaveLength(1);
    expect(state.settles[0]!.status).toBe("failed");
    expect(state.settles[0]!.error_code).toBe("POLICY_TOOL_BLOCKED");
    const events = state.eventsByRun.get(runKey) ?? [];
    expect(events.some((e) => e.type === "run.failed")).toBe(true);
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
    expect(JSON.stringify(wirePayloads)).not.toContain("sk-integration-cloud-key");
    expect(JSON.stringify(state.eventsByRun.get(runKey) ?? [])).not.toContain(
      "sk-integration-cloud-key",
    );
    expect(state.settles).toHaveLength(1);
    expect(state.settles[0]!.status).toBe("failed");
    expect(state.settles[0]!.error_code).toBe("POLICY_SECRET_LEAK");
  }, 30000);

  it("refuses explainably when provider policy_result is not allowed (no model loop)", async () => {
    resetState();
    const runKey = enqueueJob({
      allowlist: ["familygraph.echo"],
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

    // Explainable refusal: no model loop, no tool call, no user_added event.
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
    expect(events.map((e) => e.type)).toEqual(["run.failed"]);
    expect(events[0]!.public_payload).toMatchObject({ error_code: "PROVIDER_DENIED_NO_LOCAL" });
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
