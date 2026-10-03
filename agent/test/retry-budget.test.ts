/**
 * Run-level retry budget: bounds the TRUE outbound attempt count for one run.
 *
 * WHY this exists: the request layer (pi-ai `retryProviderRequest`) and the
 * session layer (Pi auto-retry) are configured separately and cannot see each
 * other's consumption, so their budgets multiply. With the shipped values that
 * is `(5+1) x (3+1) = 24` real attempts for one transient failure — measured on
 * the real development database: failed runs have p50 = p90 = p99 = 24 egress
 * rows, while successful runs have p50 = 3.
 *
 * The budget is charged in the transport because that is the only place both
 * layers funnel through. These tests use the REAL SDK against a local fake
 * gateway, so they prove the wiring, not a mock of it.
 */
import { createServer, type Server } from "node:http";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { InternalClient, type RunContextProjection } from "../src/client.js";
import { buildRunSession, SESSION_RETRY_BUDGET, type SessionBundle } from "../src/session.js";
import {
  RunRetryBudget,
  RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE,
  RUN_RETRY_BUDGET_HTTP_STATUS,
  classifyProviderStreamError,
  resolveRetryBudgetLimits,
} from "../src/retry-budget.js";
import { makeAgentConfig } from "./helpers.js";

const RUN_ID = "42";

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

function transientFailure(): { status: number; headers: Record<string, string>; body: string } {
  return {
    status: 502,
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      error: { code: "AGENT_PROVIDER_PROXY_UNAVAILABLE", message: "Provider 返回错误" },
    }),
  };
}

function successAnswer(): { status: number; headers: Record<string, string>; body: string } {
  return {
    status: 200,
    headers: { "content-type": "text/event-stream" },
    body:
      'data: {"id":"c1","choices":[{"delta":{"content":"The tin is on the shelf."},"finish_reason":null}]}\n\n' +
      'data: {"id":"c1","choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":6}}\n\n' +
      "data: [DONE]\n\n",
  };
}

interface FakeGateway {
  server: Server;
  port: number;
  requests: number;
}

async function startGateway(
  answer: () => { status: number; headers: Record<string, string>; body: string },
): Promise<FakeGateway> {
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

describe("RunRetryBudget (unit)", () => {
  it("charges each real attempt and refuses before the socket once exhausted", async () => {
    const budget = new RunRetryBudget({ maxProviderAttempts: 2, maxTotalMs: 60_000 });
    let inner = 0;
    const wrapped = budget.wrapFetch((async () => {
      inner += 1;
      return new Response("ok");
    }) as typeof globalThis.fetch);

    await wrapped("http://example.invalid/1");
    await wrapped("http://example.invalid/2");
    // Third attempt must be refused WITHOUT reaching the transport: a budget
    // rejection that still opened a socket would keep generating the very
    // traffic the budget exists to stop.
    const refusal = await wrapped("http://example.invalid/3");
    expect(inner).toBe(2);
    // A permanent 4xx is declined by both retry layers, so the refusal must not
    // look like a transient transport fault.
    expect(refusal.status).toBe(RUN_RETRY_BUDGET_HTTP_STATUS);
    const refusalBody = (await refusal.json()) as { error: { message: string } };
    expect(refusalBody.error.message).toBe(RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE);
    expect(budget.snapshot().exhaustedBy).toBe("attempts");
  });

  it("stops on elapsed wall clock, not only on attempt count", async () => {
    let nowMs = 1_000;
    const budget = new RunRetryBudget(
      { maxProviderAttempts: 100, maxTotalMs: 50 },
      () => nowMs,
    );
    const wrapped = budget.wrapFetch((async () => new Response("ok")) as typeof globalThis.fetch);
    await wrapped("http://example.invalid/1");
    nowMs += 51;
    const refusal = await wrapped("http://example.invalid/2");
    expect(refusal.status).toBe(RUN_RETRY_BUDGET_HTTP_STATUS);
    expect(budget.snapshot().exhaustedBy).toBe("elapsed");
  });

  it("is disabled by a zero limit, so the default path is unchanged", () => {
    expect(resolveRetryBudgetLimits({ runMaxProviderAttempts: 0, runMaxTotalRetryMs: 0 })).toBeNull();
    expect(
      resolveRetryBudgetLimits({ runMaxProviderAttempts: 8, runMaxTotalRetryMs: 0 }),
    ).toBeNull();
  });
});

describe("run retry budget against the real SDK", () => {
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
    budget: RunRetryBudget | undefined,
  ): Promise<{ events: AgentSessionEvent[]; session: SessionBundle["session"] }> {
    const config = makeAgentConfig(gateway.port);
    config.internalApiBaseUrl = `http://127.0.0.1:${gateway.port}`;
    config.providerStreamMaxRetries = 5;
    config.providerStreamMaxRetryDelayMs = 2_000;
    const client = new InternalClient(config);
    const agentDir = mkdtempSync(join(tmpdir(), "fg-budget-test-"));
    dirs.push(agentDir);
    const bundle = await buildRunSession(config, client, projection(), () => "synthetic-run-token", {
      agentDir,
      // Layer 2 at the shipped count; only the delay shrinks for test speed.
      sessionRetrySettings: { ...SESSION_RETRY_BUDGET, baseDelayMs: 5 },
      retryBudget: budget,
    });
    sessions.push(bundle.session);
    const events: AgentSessionEvent[] = [];
    bundle.session.subscribe((event) => events.push(event));
    return { events, session: bundle.session };
  }

  it("bounds the two multiplied layers to the run budget", async () => {
    // Without the budget this exact setup sends (5+1) x (3+1) = 24 attempts.
    // With maxProviderAttempts = 4 the run must stop at 4.
    const gateway = await startGateway(transientFailure);
    gateways.push(gateway);
    const budget = new RunRetryBudget({ maxProviderAttempts: 4, maxTotalMs: 600_000 });
    const { session } = await build(gateway, budget);

    await session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });

    expect(gateway.requests).toBe(4);
    expect(budget.providerAttempts).toBe(4);
  });

  it("does not retry after the budget is exhausted (no attempt beyond the cap)", async () => {
    // The decisive property: once the cap is hit, NEITHER layer may issue
    // another real attempt. A message that matched the SDK's transient pattern
    // would restart the turn and the count would exceed the cap.
    const gateway = await startGateway(transientFailure);
    gateways.push(gateway);
    const budget = new RunRetryBudget({ maxProviderAttempts: 2, maxTotalMs: 600_000 });
    const { events, session } = await build(gateway, budget);

    await session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });

    expect(gateway.requests).toBe(2);
    const errors = events
      .filter(
        (event) =>
          event.type === "message_end" &&
          (event as { message: { role: string } }).message.role === "assistant",
      )
      .map((event) => (event as { message: { errorMessage?: string } }).message.errorMessage);
    expect(errors.at(-1)).toContain(RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE);
  });

  it("leaves a healthy run untouched (budget is a ceiling, not a quota)", async () => {
    const gateway = await startGateway(successAnswer);
    gateways.push(gateway);
    const budget = new RunRetryBudget({ maxProviderAttempts: 8, maxTotalMs: 120_000 });
    const { session } = await build(gateway, budget);

    await session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });

    expect(gateway.requests).toBe(1);
    expect(budget.providerAttempts).toBe(1);
    expect(budget.snapshot().exhausted).toBe(false);
  });

  it("does not consume a run's budget with a second run's attempts", async () => {
    // Budgets are per run: sharing one would let a noisy run starve another.
    const gateway = await startGateway(successAnswer);
    gateways.push(gateway);
    const first = new RunRetryBudget({ maxProviderAttempts: 8, maxTotalMs: 120_000 });
    const second = new RunRetryBudget({ maxProviderAttempts: 8, maxTotalMs: 120_000 });
    const firstRun = await build(gateway, first);
    await firstRun.session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });
    const secondRun = await build(gateway, second);
    await secondRun.session.prompt("Where is the blue tin?", {
      source: "rpc",
      expandPromptTemplates: false,
    });

    expect(first.providerAttempts).toBe(1);
    expect(second.providerAttempts).toBe(1);
  });
});


describe("classifyProviderStreamError", () => {
  it("reports the budget code only when the budget really ran out", () => {
    // Message-only would trust redacted upstream text; budget-only would blame
    // the budget for an unrelated post-attempt failure. Requiring both keeps the
    // distinction meaningful.
    expect(
      classifyProviderStreamError({
        message: `... ${RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE} ...`,
        budgetExhausted: true,
      }),
    ).toBe("PROVIDER_RETRY_BUDGET_EXHAUSTED");
    expect(
      classifyProviderStreamError({
        message: `... ${RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE} ...`,
        budgetExhausted: false,
      }),
    ).toBe("PROVIDER_STREAM_ERROR");
    expect(
      classifyProviderStreamError({ message: "upstream 503", budgetExhausted: true }),
    ).toBe("PROVIDER_STREAM_ERROR");
  });
});
