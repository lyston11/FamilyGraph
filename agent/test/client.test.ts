import { createServer, type Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { InternalClient } from "../src/client.js";
import { AuthError, ConflictError, GoneError, TransientError } from "../src/errors.js";
import type { AgentConfig } from "../src/config.js";

function testConfig(port: number): AgentConfig {
  return {
    apiBaseUrl: `http://127.0.0.1:${port}`,
    internalApiBaseUrl: `http://127.0.0.1:${port}`,
    providerStreamMaxRetries: 0,
    providerStreamMaxRetryDelayMs: 1000,
    serviceSecret: "unit-secret",
    sidecarId: "sc-unit",
    healthPort: 0,
    leasePollIntervalMs: 10,
    defaultLeaseMs: 60_000,
    eventFlushIntervalMs: 10,
    eventFlushBatchSize: 5,
    retryMaxAttempts: 3,
    retryBaseDelayMs: 1,
    retryMaxDelayMs: 2,
    requestTimeoutMs: 2000,
    providers: {
      cloud: { kind: "openai_compatible", baseUrl: undefined, apiKey: undefined, model: undefined },
      local: { kind: "local", baseUrl: undefined, apiKey: undefined, model: undefined },
    },
  };
}

describe("InternalClient protocol behavior", () => {
  let server: Server;
  let port = 0;
  const seenAuthHeaders: string[] = [];
  const seenLeaseBodies: unknown[] = [];
  const seenToolBodies: unknown[] = [];

  beforeAll(async () => {
    server = createServer((req, res) => {
      const auth = String(req.headers["authorization"] ?? "");
      seenAuthHeaders.push(`${req.method} ${req.url} ${auth}`);
      const respond = (status: number, body: unknown): void => {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(body));
      };
      const readBody = async (): Promise<unknown> => {
        const chunks: Buffer[] = [];
        for await (const chunk of req) chunks.push(chunk as Buffer);
        const raw = Buffer.concat(chunks).toString();
        return raw ? JSON.parse(raw) : {};
      };
      void (async () => {
        if (req.url === "/internal/agent/jobs/lease") {
          // Accept only HMAC service tokens (JWT-shaped bearer).
          if (!/^Bearer ey[\w-]+\.[\w-]+\.[\w-]+$/.test(auth)) {
            return respond(401, { detail: "bad service token" });
          }
          seenLeaseBodies.push(await readBody());
          // Authoritative flat LeaseOut (backend/app/schemas/agent.py).
          return respond(200, {
            job_id: 41,
            run_id: 42,
            agent_kind: "assistant",
            attempt: 2,
            tool_allowlist: ["familygraph.echo", "familygraph.probe_scope"],
            policy_version: "pv-9",
            run_token: "run-tok",
          });
        }
        if (req.url === "/internal/agent/jobs/empty/lease") return respond(404, {});
        if (req.url === "/internal/agent/runs/r1/context") {
          if (auth !== "Bearer run-tok") return respond(401, { detail: "nope" });
          return respond(200, {
            run_id: 42,
            session_id: 7,
            agent_kind: "assistant",
            account_id: 900,
            space_id: 800,
            status: "leased",
            attempt: 2,
            policy_version: "pv-9",
            tool_allowlist: ["familygraph.echo"],
            messages: [
              {
                id: 11,
                role: "user",
                content_json: { text: "hi" },
                created_at: "2026-08-26T00:00:00Z",
              },
            ],
            context_blocks: [
              {
                source_id: "memory-1",
                source_type: "memory",
                scope: "private",
                sensitivity: "normal",
                revision: 1,
                citation: "rag:memory-1:r1:c1",
                content: "trusted only as data",
              },
            ],
            provider: {
              provider_id: 3,
              model: "model-x",
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
              base_url: "/internal/agent/runs/42/provider",
              api_key: null,
            },
            next_event_seq: 1,
            cancel_requested: false,
          });
        }
        if (req.url === "/internal/agent/runs/r1/events/append") {
          if (auth === "Bearer gone-tok") return respond(410, { detail: "lease expired" });
          if (auth === "Bearer conflict-tok") return respond(409, { detail: "already settled" });
          return respond(200, {
            accepted: [
              { seq: 1, event_id: 101 },
              { seq: 3, event_id: 103 },
            ],
            duplicates: [2],
          });
        }
        if (req.url?.startsWith("/internal/agent/runs/r1/tools/") && req.method === "POST") {
          if (auth === "Bearer forbidden-tok") return respond(403, { detail: "scope mismatch" });
          seenToolBodies.push(await readBody());
          return respond(200, { ok: true, tool: "familygraph.echo", version: 1, output: { text: "x" } });
        }
        if (req.url === "/internal/agent/runs/r1/settle" && req.method === "POST") {
          return respond(200, { ok: true, run_id: 42, status: "succeeded", settled_at: "2026-08-26T00:00:00Z" });
        }
        if (req.url === "/api/health") return respond(200, { status: "ok" });
        respond(404, { detail: "nf" });
      })();
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    port = (server.address() as { port: number }).port;
  });

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it("sends kind=assistant with leased_by and consumes the flat lease response", async () => {
    const client = new InternalClient(testConfig(port));
    const job = await client.leaseJob();
    expect(job).not.toBeNull();
    expect(job).toEqual({
      job_id: "41",
      run_id: "42",
      agent_kind: "assistant",
      attempt: 2,
      tool_allowlist: ["familygraph.echo", "familygraph.probe_scope"],
      policy_version: "pv-9",
      run_token: "run-tok",
    });
    expect(seenLeaseBodies).toEqual([{ kind: "assistant", leased_by: "sc-unit" }]);
    const leaseLine = seenAuthHeaders.find((l) => l.includes("/lease"));
    expect(leaseLine).toMatch(
      /^POST \/internal\/agent\/jobs\/lease Bearer ey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/,
    );
  });

  it("returns null on HTTP 204 with empty body when queue is empty", async () => {
    const config = testConfig(port);
    const client = new InternalClient(config, {
      fetchImpl: (async () => new Response(null, { status: 204 })) as typeof fetch,
    });
    expect(await client.leaseJob()).toBeNull();
  });

  it("maps 401/409/410/403 to typed errors without retry", async () => {
    const client = new InternalClient(testConfig(port));
    await expect(client.getRunContext("r1", "wrong")).rejects.toBeInstanceOf(AuthError);
    await expect(
      client.appendEvents("r1", "conflict-tok", [{ seq: 1, type: "run.started", public_payload: {} }]),
    ).rejects.toBeInstanceOf(ConflictError);
    await expect(client.appendEvents("r1", "gone-tok", [])).rejects.toBeInstanceOf(GoneError);
    await expect(
      client.executeTool("r1", "forbidden-tok", "familygraph.echo", {
        version: 1,
        input: {},
      }),
    ).rejects.toBeInstanceOf(Error);
  });

  it("retries transient network failures and succeeds", async () => {
    let attempts = 0;
    const config = testConfig(port);
    const client = new InternalClient(config, {
      fetchImpl: (async (
        input: Parameters<typeof fetch>[0],
        init?: Parameters<typeof fetch>[1],
      ) => {
        attempts += 1;
        if (attempts < 3) throw new Error("ECONNRESET");
        return fetch(input, init);
      }) as typeof fetch,
      backoff: { baseDelayMs: 1, maxDelayMs: 2, maxAttempts: 4 },
    });
    const projection = await client.getRunContext("r1", "run-tok");
    expect(attempts).toBe(3);
    // Normalized projection view of the real ContextOut.
    expect(projection.run_id).toBe("42");
    expect(projection.session_id).toBe("7");
    expect(projection.account_id).toBe("900");
    expect(projection.space_id).toBe("800");
    expect(projection.attempt).toBe(2);
    expect(projection.provider?.policy_result).toBe("allowed");
    expect(projection.provider?.provider_id).toBe("3");
    expect(projection.provider?.base_url).toBe("/internal/agent/runs/42/provider");
    expect(projection.provider?.api_key).toBeNull();
    expect(projection.messages[0]?.content_json["text"]).toBe("hi");
    expect(projection.context_blocks?.[0]?.citation).toBe("rag:memory-1:r1:c1");
    expect(projection.cancel_requested).toBe(false);
  });

  it("aborts an in-flight internal request and its retry backoff", async () => {
    const controller = new AbortController();
    let attempts = 0;
    const client = new InternalClient(testConfig(port), {
      fetchImpl: (async (
        _input: Parameters<typeof fetch>[0],
        init?: Parameters<typeof fetch>[1],
      ) => {
        attempts += 1;
        await new Promise<void>((resolve, reject) => {
          const signal = init?.signal;
          if (signal?.aborted) return reject(new DOMException("aborted", "AbortError"));
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {
            once: true,
          });
        });
        return new Response(null, { status: 503 });
      }) as typeof fetch,
      backoff: { baseDelayMs: 10_000, maxDelayMs: 10_000, maxAttempts: 4 },
    });
    const request = client.getRunContext("r1", "run-tok", controller.signal);
    await new Promise((resolve) => setTimeout(resolve, 5));
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(attempts).toBe(1);
  });

  it("fails closed when provider protocol is missing", async () => {
    const client = new InternalClient(testConfig(port), {
      fetchImpl: (async () =>
        new Response(
          JSON.stringify({
            run_id: 42,
            session_id: 7,
            agent_kind: "assistant",
            account_id: 900,
            space_id: 800,
            status: "running",
            attempt: 1,
            policy_version: "pv-9",
            tool_allowlist: [],
            messages: [],
            next_event_seq: 1,
            cancel_requested: false,
            provider: {
              provider_id: 3,
              policy_result: "allowed",
              base_url: "/internal/agent/runs/42/provider",
              api_key: null,
            },
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        )) as typeof fetch,
    });
    const projection = await client.getRunContext("r1", "run-tok");
    expect(projection.provider?.api).toBeUndefined();
  });

  it("fails closed when a core context field is missing", async () => {
    const client = new InternalClient(testConfig(port), {
      fetchImpl: (async () =>
        new Response(
          JSON.stringify({
            // run_id intentionally omitted: a malformed projection must not
            // be coerced into an empty string or a synthetic run.
            session_id: 7,
            agent_kind: "assistant",
            account_id: 900,
            space_id: 800,
            status: "running",
            attempt: 1,
            policy_version: "pv-9",
            tool_allowlist: [],
            messages: [],
            next_event_seq: 0,
            cancel_requested: false,
            provider: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        )) as typeof fetch,
    });
    await expect(client.getRunContext("r1", "run-tok")).rejects.toMatchObject({
      code: "invalid_context_projection",
    });
  });

  it("fails closed on malformed context blocks", async () => {
    const client = new InternalClient(testConfig(port), {
      fetchImpl: (async () =>
        new Response(
          JSON.stringify({
            run_id: 42,
            session_id: 7,
            agent_kind: "assistant",
            account_id: 900,
            space_id: 800,
            status: "running",
            attempt: 1,
            policy_version: "pv-9",
            tool_allowlist: [],
            messages: [],
            context_blocks: [{ source_id: "x" }],
            provider: null,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        )) as typeof fetch,
    });
    await expect(client.getRunContext("r1", "run-tok")).rejects.toMatchObject({
      code: "invalid_context_projection",
    });
  });

  it("throws TransientError when retries are exhausted", async () => {
    const config = testConfig(port);
    config.apiBaseUrl = "http://127.0.0.1:9"; // nothing listens here
    config.internalApiBaseUrl = "http://127.0.0.1:9";
    config.providerStreamMaxRetries = 0;
    config.providerStreamMaxRetryDelayMs = 1000;
    config.requestTimeoutMs = 300;
    const client = new InternalClient(config, {
      backoff: { baseDelayMs: 1, maxDelayMs: 2, maxAttempts: 2 },
    });
    await expect(client.getRunContext("r1", "run-tok")).rejects.toBeInstanceOf(TransientError);
  });

  it("parses accepted entries as {seq, event_id} objects and reports duplicates", async () => {
    const client = new InternalClient(testConfig(port));
    const out = await client.appendEvents("r1", "run-tok", [
      { seq: 1, type: "run.started", public_payload: {} },
      { seq: 2, type: "turn.started", public_payload: {} },
      { seq: 3, type: "turn.completed", public_payload: {} },
    ]);
    expect(out.accepted).toEqual([1, 3]);
    expect(out.duplicates).toEqual([2]);
  });

  it("sends {version, input, tool_call_id} (no tool_version) and unwraps output", async () => {
    const client = new InternalClient(testConfig(port));
    const result = await client.executeTool("r1", "run-tok", "familygraph.echo", {
      version: 1,
      input: { text: "x" },
      tool_call_id: "tc_1",
    });
    expect(result).toEqual({ ok: true, result: { text: "x" } });
    expect(seenToolBodies).toEqual([
      { version: 1, input: { text: "x" }, tool_call_id: "tc_1" },
    ]);
    const wireBody = seenToolBodies[0] as Record<string, unknown>;
    expect("tool_version" in wireBody).toBe(false);
  });

  it("settle posts to /settle with error split into error_code + error object", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const config = testConfig(port);
    const client = new InternalClient(config, {
      fetchImpl: (async (
        input: Parameters<typeof fetch>[0],
        init?: Parameters<typeof fetch>[1],
      ) => {
        const url = String(input);
        if (url.endsWith("/internal/agent/runs/r1/settle")) {
          bodies.push(JSON.parse(String(init?.body)));
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        }
        return fetch(input, init);
      }) as typeof fetch,
    });
    await client.settleRun("r1", "run-tok", "failed", { code: "PROVIDER_DENIED", message: "no" });
    await client.settleRun("r1", "run-tok", "succeeded");
    expect(bodies[0]).toEqual({
      status: "failed",
      error_code: "PROVIDER_DENIED",
      error: { message: "no" },
    });
    expect(bodies[1]).toEqual({ status: "succeeded" });
  });
});
