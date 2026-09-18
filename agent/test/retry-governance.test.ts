/**
 * E-AC2: two-layer retry governance against the REAL Pi SDK and a local fake
 * gateway. No network, no model service — the gateway is a node:http server
 * that answers exactly like `app/services/provider_proxy.py` does.
 *
 * Layer 1 (request): pi-ai `retryProviderRequest`, driven by
 * `AGENT_PROVIDER_STREAM_MAX_RETRIES`; it only retries 408/409/429/5xx and
 * honours `x-should-retry: false`.
 * Layer 2 (session): Pi's auto-retry on a failed assistant turn, frozen by
 * `SESSION_RETRY_BUDGET`; it retries on transient error **text** only.
 *
 * Measured with this SDK (0.84.3): the two layers multiply — a transient
 * failure is sent (requestRetries + 1) x (sessionRetries + 1) times. Two
 * consequences are pinned here because the gateway must not rely on guesses:
 *
 *  1. `x-should-retry: false` stops layer 1 only. Layer 2 looks at the error
 *     message, so a 5xx body still restarts the whole turn (see the
 *     "stop header only binds the request layer" case). The gateway therefore
 *     answers a permanent upstream rejection with its real 4xx status instead
 *     of a 502 + header.
 *  2. An error message that does not match pi-ai's transient pattern
 *     (e.g. a local policy refusal) is never retried by either layer.
 */
import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import { SettingsManager } from "@earendil-works/pi-coding-agent";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { InternalClient, type RunContextProjection } from "../src/client.js";
import { buildRunSession, SESSION_RETRY_BUDGET, type SessionBundle, type SessionRetryBudget } from "../src/session.js";
import { makeAgentConfig } from "./helpers.js";

const RUN_ID = "42";
const REQUEST_RETRIES = 2;
const SESSION_RETRIES = 3;

function projection(): RunContextProjection {
  return {
    run_id: RUN_ID,
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
      provider_name: "retry-provider",
      model: "retry-model",
      kind: "local",
      api: "openai-completions",
      context_window: 272_000,
      max_tokens: 4_096,
      reasoning: false,
      input_modalities: ["text"],
      thinking_levels: [],
      policy_result: "allowed",
      secret_ref: null,
      base_url: `/internal/agent/runs/${RUN_ID}/provider`,
      api_key: null,
    },
    cancel_requested: false,
  };
}

interface GatewayAnswer {
  status: number;
  headers: Record<string, string>;
  body: string;
}

interface FakeGateway {
  server: Server;
  port: number;
  requests: number;
}

/** Mirrors the gateway's safe, redacted error envelopes (no upstream text). */
function permanentRejection(): GatewayAnswer {
  return {
    status: 400,
    headers: { "content-type": "application/json", "x-should-retry": "false" },
    body: JSON.stringify({
      error: { code: "AGENT_PROVIDER_UPSTREAM_REJECTED", message: "Provider 拒绝了本次请求" },
    }),
  };
}

function transient503WithStopHeader(): GatewayAnswer {
  return {
    status: 503,
    headers: { "content-type": "application/json", "x-should-retry": "false" },
    body: JSON.stringify({
      error: { code: "AGENT_PROVIDER_PROXY_UNAVAILABLE", message: "Provider 返回错误" },
    }),
  };
}

function transientFailure(): GatewayAnswer {
  return {
    status: 502,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      error: { code: "AGENT_PROVIDER_PROXY_UNAVAILABLE", message: "Provider 返回错误" },
    }),
  };
}

function successAnswer(): GatewayAnswer {
  return {
    status: 200,
    headers: { "content-type": "text/event-stream" },
    body:
      'data: {"id":"c1","choices":[{"delta":{"content":"The tin is on the shelf."},"finish_reason":null}]}\n\n' +
      'data: {"id":"c1","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":6}}\n\n' +
      "data: [DONE]\n\n",
  };
}

async function startGateway(answer: () => GatewayAnswer): Promise<FakeGateway> {
  const gateway: FakeGateway = {
    server: createServer((_req, res) => {
      gateway.requests += 1;
      const { status, headers, body } = answer();
      res.writeHead(status, headers);
      res.end(body);
    }),
    port: 0,
    requests: 0,
  };
  await new Promise<void>((resolve) => gateway.server.listen(0, "127.0.0.1", resolve));
  const address = gateway.server.address();
  if (address === null || typeof address === "string") throw new Error("no port");
  gateway.port = address.port;
  return gateway;
}

describe("assistant retry governance against the real SDK", () => {
  const sessions: SessionBundle["session"][] = [];
  const dirs: string[] = [];
  const gateways: FakeGateway[] = [];

  afterEach(async () => {
    for (const session of sessions.splice(0)) session.dispose();
    for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
    for (const gateway of gateways.splice(0)) {
      await new Promise<void>((resolve) => gateway.server.close(() => resolve()));
    }
  });

  async function build(
    gateway: FakeGateway,
    options: { signal?: AbortSignal; sessionRetrySettings?: SessionRetryBudget } = {},
  ): Promise<{ events: AgentSessionEvent[]; session: SessionBundle["session"] }> {
    const config = makeAgentConfig(gateway.port);
    // The registered provider's baseUrl comes from the projection and is
    // resolved against `internalApiBaseUrl`, so the gateway is the only
    // possible egress target in this test.
    config.internalApiBaseUrl = `http://127.0.0.1:${gateway.port}`;
    config.providerStreamMaxRetries = REQUEST_RETRIES;
    // Keep the layer-1 backoff short but still real (0.5s, 1s).
    config.providerStreamMaxRetryDelayMs = 2_000;
    const client = new InternalClient(config);
    const agentDir = mkdtempSync(join(tmpdir(), "fg-retry-test-"));
    dirs.push(agentDir);
    const bundle = await buildRunSession(config, client, projection(), "synthetic-run-token", {
      agentDir,
      signal: options.signal,
      // Layer 2 stays at the shipped budget count; only the delay shrinks so
      // the test is fast (the multiplication is what matters here).
      sessionRetrySettings:
        options.sessionRetrySettings ?? { ...SESSION_RETRY_BUDGET, baseDelayMs: 5 },
    });
    sessions.push(bundle.session);
    const events: AgentSessionEvent[] = [];
    bundle.session.subscribe((event) => events.push(event));
    return { events, session: bundle.session };
  }

  it("freezes the shipped session retry budget against SDK drift", async () => {
    // The budget is declared in the sidecar, not inherited from the SDK: a Pi
    // upgrade that changes its default must fail this test so the change is a
    // deliberate policy decision (PRD E-R3/E-R5), not a silent egress change.
    const sdkDefault = SettingsManager.inMemory().getRetrySettings();
    expect(sdkDefault.enabled).toBe(SESSION_RETRY_BUDGET.enabled);
    expect(sdkDefault.maxRetries).toBe(SESSION_RETRY_BUDGET.maxRetries);
    expect(sdkDefault.baseDelayMs).toBe(SESSION_RETRY_BUDGET.baseDelayMs);
  });

  it("honours the declared session budget (wiring, not decoration)", async () => {
    const gateway = await startGateway(() => transientFailure());
    gateways.push(gateway);
    const { events, session } = await build(gateway, {
      sessionRetrySettings: { ...SESSION_RETRY_BUDGET, maxRetries: 0, baseDelayMs: 5 },
    });

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    // Layer 2 disabled: only the request layer's per-turn budget applies.
    expect(gateway.requests).toBe(REQUEST_RETRIES + 1);
    expect(events.some((event) => event.type === "auto_retry_start")).toBe(false);
  });

  it("recovers within one turn when a single request fails then succeeds", async () => {
    // E-AC2: one failure then success. Layer 1 retries once inside the same
    // turn; layer 2 never restarts the turn. Two egress attempts, one answer.
    let attempt = 0;
    const gateway = await startGateway(() => {
      attempt += 1;
      return attempt === 1 ? transientFailure() : successAnswer();
    });
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    expect(gateway.requests).toBe(2);
    expect(events.some((event) => event.type === "auto_retry_start")).toBe(false);
    const answers = events.filter(
      (event) => event.type === "message_end" && event.message.role === "assistant",
    );
    expect(answers).toHaveLength(1);
  });

  it("retries a truncated stream at the session layer, not the request layer", async () => {
    // E-AC2/E-AC3: headers already reached the sidecar, so layer 1 has no error
    // to classify (status 200) and does not retry the request. The truncated
    // stream surfaces as a transient error text, so layer 2 restarts the turn
    // sessionRetries times. The gateway side of this failure (sent=true,
    // stream_interrupted) is covered in test_provider_proxy.py.
    const gateway = await startGateway(() => {
      return {
        status: 200,
        headers: { "content-type": "text/event-stream" },
        // Truncated SSE: no terminal event, connection just ends.
        body: 'data: {"choices":[{"delta":{"content":"par"}}]}\n\n',
      };
    });
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    expect(gateway.requests).toBe(SESSION_RETRIES + 1);
    expect(events.filter((event) => event.type === "auto_retry_start")).toHaveLength(SESSION_RETRIES);
  });

  it("keeps a permanent upstream rejection non-retryable at both layers", async () => {
    // The gateway answers a permanent upstream rejection with its real 4xx
    // status: layer 1 never retries 4xx and layer 2's transient text pattern
    // does not match the redacted envelope. Exactly one egress attempt.
    const gateway = await startGateway(() => permanentRejection());
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    expect(gateway.requests).toBe(1);
    expect(events.some((event) => event.type === "auto_retry_start")).toBe(false);
  });

  it("stop header only binds the request layer, so 5xx must stay transient", async () => {
    // Pins WHY the gateway maps a permanent rejection to 4xx rather than to a
    // 502 carrying `x-should-retry: false`: the header silences layer 1, but
    // layer 2 still restarts the turn on the 5xx error text. If this ever
    // changes, the gateway classification can be revisited deliberately.
    const gateway = await startGateway(() => transient503WithStopHeader());
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    expect(gateway.requests).toBe(SESSION_RETRIES + 1);
    expect(events.filter((event) => event.type === "auto_retry_start")).toHaveLength(SESSION_RETRIES);
  });

  it("sends a permanent upstream rejection exactly once", async () => {
    const gateway = await startGateway(() => permanentRejection());
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    // Layer 1 stops immediately (x-should-retry:false); layer 2 sees a
    // non-retryable 400 envelope and does not restart the turn.
    expect(gateway.requests).toBe(1);
    expect(events.some((event) => event.type === "auto_retry_start")).toBe(false);
  });

  it("multiplies the two layers for a transient upstream failure", async () => {
    const gateway = await startGateway(() => transientFailure());
    gateways.push(gateway);
    const { events, session } = await build(gateway);

    await session.prompt("Where is the blue tin?", { source: "rpc", expandPromptTemplates: false });

    // Layer 1: maxRetries + 1 per turn. Layer 2: sessionMaxRetries + 1 turns.
    const perTurn = REQUEST_RETRIES + 1;
    const turns = SESSION_RETRIES + 1;
    expect(gateway.requests).toBe(perTurn * turns);
    const retryStarts = events.filter((event) => event.type === "auto_retry_start");
    expect(retryStarts).toHaveLength(SESSION_RETRIES);
  });

  it("aborts both layers when the run is cancelled during backoff", async () => {
    const gateway = await startGateway(() => transientFailure());
    gateways.push(gateway);
    const controller = new AbortController();
    const { session } = await build(gateway, { signal: controller.signal });

    const prompted = session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });
    // Cancel while the request layer is backing off: no further egress may
    // happen after the abort, and the prompt must resolve (never hang).
    setTimeout(() => controller.abort(), 20);
    await prompted;

    const afterAbort = gateway.requests;
    expect(afterAbort).toBeLessThan(REQUEST_RETRIES + 1);
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(gateway.requests).toBe(afterAbort);
  });
});
