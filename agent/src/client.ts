/**
 * FastAPI internal protocol client (design.md internal endpoints).
 *
 * Auth is two-tier per notes.md:
 *  - lease: short-lived HMAC service token minted from AGENT_SERVICE_SECRET;
 *  - everything else: the opaque run token issued in the lease response.
 *
 * Wire shapes mirror backend/app/schemas/agent.py exactly (strict
 * extra=forbid): lease body is {leased_by} only, lease responses are flat,
 * an empty queue is HTTP 204 with no body, events live under /events/append,
 * settle lives under /settle and only accepts "succeeded"|"failed" —
 * cancellation is adjudicated by FastAPI, never by the sidecar.
 *
 * Retry semantics: only transient failures (network errors, 502/503/504) are
 * retried with exponential backoff. V2.1 registers ONLY read-only tools, so
 * retries cannot repeat side effects — there is no server-side tool dedupe
 * table yet (lands with the first write tool in V2.4). Do NOT register a
 * side-effectful tool before that table exists. Event appends are idempotent
 * on (run_id, seq). 4xx responses are terminal and surface as typed errors
 * — never retried.
 */

import { backoffDelayMs, sleep, type BackoffPolicy } from "./backoff.js";
import type { AgentConfig } from "./config.js";
import type { FgEvent } from "./events.js";
import {
  AuthError,
  ConflictError,
  ForbiddenError,
  GoneError,
  InternalApiError,
  TransientError,
} from "./errors.js";
import { signServiceToken } from "./tokens.js";

/**
 * Flat lease response (backend LeaseOut). Numeric ids are converted to
 * strings; lease duration is NOT advertised by FastAPI — callers use
 * config.defaultLeaseMs.
 */
export interface LeasedJob {
  job_id: string;
  run_id: string;
  agent_kind: "assistant" | "steward";
  attempt: number;
  tool_allowlist: string[];
  policy_version: string;
  run_token: string;
}

/** One persisted session message as projected by GET /runs/{id}/context. */
export interface RunContextMessage {
  id: number;
  role: string;
  content_json: Record<string, unknown>;
  created_at: string;
}

export type ProviderPolicyResult =
  | "allowed"
  | "denied"
  | "denied_no_local"
  | "denied_cloud_forbidden";

/** Server-side provider resolution (ContextProviderOut), including the
 * ProviderGateway runtime config (base_url + decrypted api_key) injected over
 * the internal listener. api_key/base_url must never be logged or persisted. */
export interface RunContextProvider {
  provider_id: string;
  model: string | null;
  kind: "openai_compatible" | "local" | null;
  policy_result: ProviderPolicyResult;
  secret_ref: string | null;
  base_url: string | null;
  api_key: string | null;
}

/**
 * Normalized view of ContextOut: ids coerced to strings for internal use;
 * field names otherwise mirror the wire contract exactly. There is no actor
 * object and no system prompt — the system prompt is a sidecar-local constant
 * (domain prompts arrive in V2.2+).
 */
export interface RunContextBlock {
  source_id: string;
  source_type: string;
  scope: string;
  sensitivity: string;
  revision: number;
  citation: string;
  content: string;
}

export interface RunContextProjection {
  run_id: string;
  session_id: string;
  agent_kind: "assistant" | "steward";
  account_id: string;
  space_id: string;
  status: string;
  attempt: number;
  policy_version: string;
  tool_allowlist: string[];
  messages: RunContextMessage[];
  context_blocks?: RunContextBlock[];
  provider: RunContextProvider | null;
  cancel_requested: boolean;
}

export interface ToolExecutionResult {
  ok: true;
  result: unknown;
}

export interface InternalClientOptions {
  fetchImpl?: typeof fetch;
  backoff?: BackoffPolicy;
  nowMs?: () => number;
}

/**
 * Normalize a raw ContextOut payload into the internal projection view:
 * numeric ids become strings, missing provider resolves to null and a
 * missing policy_result fails closed to "denied".
 */
function normalizeRunContext(raw: Record<string, unknown>): RunContextProjection {
  const messages = Array.isArray(raw["messages"])
    ? (raw["messages"] as RunContextMessage[])
    : [];
  return {
    run_id: String(raw["run_id"] ?? ""),
    session_id: String(raw["session_id"] ?? ""),
    agent_kind: raw["agent_kind"] === "steward" ? "steward" : "assistant",
    account_id: String(raw["account_id"] ?? ""),
    space_id: String(raw["space_id"] ?? ""),
    status: String(raw["status"] ?? ""),
    attempt: Number(raw["attempt"] ?? 0),
    policy_version: String(raw["policy_version"] ?? ""),
    tool_allowlist: Array.isArray(raw["tool_allowlist"])
      ? (raw["tool_allowlist"] as unknown[]).map(String)
      : [],
    messages,
    context_blocks: Array.isArray(raw["context_blocks"])
      ? (raw["context_blocks"] as RunContextBlock[])
      : [],
    provider: normalizeProvider(raw["provider"]),
    cancel_requested: Boolean(raw["cancel_requested"]),
  };
}

function normalizeProvider(value: unknown): RunContextProvider | null {
  if (value === null || value === undefined || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const kind = raw["kind"];
  return {
    provider_id: raw["provider_id"] == null ? "" : String(raw["provider_id"]),
    model: raw["model"] == null ? null : String(raw["model"]),
    kind: kind === "local" || kind === "openai_compatible" ? kind : null,
    // Fail closed: an unreadable policy result must never enable inference.
    policy_result:
      raw["policy_result"] === "allowed" ||
      raw["policy_result"] === "denied_no_local" ||
      raw["policy_result"] === "denied_cloud_forbidden"
        ? raw["policy_result"]
        : "denied",
    secret_ref: raw["secret_ref"] == null ? null : String(raw["secret_ref"]),
    base_url: raw["base_url"] == null ? null : String(raw["base_url"]),
    api_key: raw["api_key"] == null ? null : String(raw["api_key"]),
  };
}

export class InternalClient {
  private readonly fetchImpl: typeof fetch;
  private readonly backoff: BackoffPolicy;
  private readonly nowMs: () => number;

  constructor(
    private readonly config: AgentConfig,
    options: InternalClientOptions = {},
  ) {
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.backoff = options.backoff ?? {
      baseDelayMs: config.retryBaseDelayMs,
      maxDelayMs: config.retryMaxDelayMs,
      maxAttempts: config.retryMaxAttempts,
    };
    this.nowMs = options.nowMs ?? (() => Date.now());
  }

  private async request(
    method: "POST" | "GET",
    path: string,
    token: string,
    body?: unknown,
  ): Promise<{ status: number; json: unknown }> {
    // P1 网络隔离：internal 协议与公开 API 分 listener；/api/health 仍走公开 base
    const base = path.startsWith("/internal/")
      ? this.config.internalApiBaseUrl
      : this.config.apiBaseUrl;
    const url = `${base}${path}`;
    let lastError: unknown;
    for (let attempt = 1; attempt <= this.backoff.maxAttempts; attempt++) {
      try {
        const response = await this.fetchImpl(url, {
          method,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          },
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(this.config.requestTimeoutMs),
        });
        if (response.ok) {
          return { status: response.status, json: await response.json().catch(() => ({})) };
        }
        if ([502, 503, 504].includes(response.status)) {
          throw new TransientError(`upstream ${response.status}`, response.status);
        }
        // Typed terminal errors — never retried.
        const text = await response.text().catch(() => "");
        throw this.mapHttpError(response.status, text);
      } catch (error) {
        if (error instanceof InternalApiError) throw error;
        lastError = error instanceof Error ? error : new Error(String(error));
        const status = (lastError as { status?: number }).status;
        const transient = status === undefined || [502, 503, 504].includes(status);
        if (!transient || attempt === this.backoff.maxAttempts) break;
        const delay = backoffDelayMs(this.backoff, attempt);
        if (delay === null) break;
        await sleep(delay);
      }
    }
    throw new TransientError(
      `internal request failed after ${this.backoff.maxAttempts} attempts: ${
        lastError instanceof Error ? lastError.message : String(lastError)
      }`,
      (lastError as { status?: number }).status,
    );
  }

  private mapHttpError(status: number, bodyText: string): InternalApiError {
    const message = `internal endpoint ${status}: ${bodyText.slice(0, 300)}`;
    if (status === 401) return new AuthError(message);
    if (status === 403) return new ForbiddenError(message);
    if (status === 409) return new ConflictError(message);
    if (status === 410) return new GoneError(message);
    return new InternalApiError(message, status, "http_error");
  }

  /** POST /internal/agent/jobs/lease — service-token authenticated, {leased_by} only. */
  async leaseJob(): Promise<LeasedJob | null> {
    const token = signServiceToken(this.config.serviceSecret, {
      sidecarId: this.config.sidecarId,
      nowMs: this.nowMs(),
    });
    const { status, json } = await this.request(
      "POST",
      "/internal/agent/jobs/lease",
      token,
      { leased_by: this.config.sidecarId },
    );
    // Empty queue: HTTP 204 with no body (request() parses it to {}).
    if (status === 204) return null;
    const raw = json as Record<string, unknown>;
    return {
      job_id: String(raw["job_id"]),
      run_id: String(raw["run_id"]),
      agent_kind: raw["agent_kind"] === "steward" ? "steward" : "assistant",
      attempt: Number(raw["attempt"] ?? 0),
      tool_allowlist: Array.isArray(raw["tool_allowlist"])
        ? (raw["tool_allowlist"] as unknown[]).map(String)
        : [],
      policy_version: String(raw["policy_version"] ?? ""),
      run_token: String(raw["run_token"] ?? ""),
    };
  }

  /**
   * POST /internal/agent/jobs/{id}/heartbeat with body {}. Returns lease
   * status plus the server-side cancel_requested flag; the worker stops
   * issuing tool calls once cancellation has been requested.
   */
  async heartbeat(
    jobId: string,
    runToken: string,
  ): Promise<{ ok: boolean; cancelRequested: boolean }> {
    const { json } = await this.request(
      "POST",
      `/internal/agent/jobs/${encodeURIComponent(jobId)}/heartbeat`,
      runToken,
      {},
    );
    const body = json as { ok?: unknown; cancel_requested?: unknown };
    return { ok: Boolean(body.ok), cancelRequested: Boolean(body.cancel_requested) };
  }

  /** GET /internal/agent/runs/{id}/context — normalized ContextOut view. */
  async getRunContext(runId: string, runToken: string): Promise<RunContextProjection> {
    const { json } = await this.request(
      "GET",
      `/internal/agent/runs/${encodeURIComponent(runId)}/context`,
      runToken,
    );
    return normalizeRunContext(json as Record<string, unknown>);
  }

  /**
   * POST /internal/agent/runs/{id}/events/append — batch append.
   * Idempotent on (run_id, seq): duplicated seqs come back in `duplicates`
   * and are treated as success (at-least-once delivery, exactly-once stream).
   * Accepted entries arrive as [{seq, event_id}] objects.
   */
  async appendEvents(
    runId: string,
    runToken: string,
    events: FgEvent[],
  ): Promise<{ accepted: number[]; duplicates: number[] }> {
    const { json } = await this.request(
      "POST",
      `/internal/agent/runs/${encodeURIComponent(runId)}/events/append`,
      runToken,
      { events },
    );
    const body = json as { accepted?: unknown; duplicates?: unknown };
    return {
      accepted: Array.isArray(body.accepted)
        ? body.accepted.map((entry) => Number((entry as { seq: unknown }).seq))
        : [],
      duplicates: Array.isArray(body.duplicates) ? body.duplicates.map(Number) : [],
    };
  }

  /**
   * POST /internal/agent/runs/{id}/tools/{tool}/execute with body
   * {version, input, tool_call_id?}. `tool_call_id` is forwarded for audit
   * traceability only in V2.1 (all tools read-only); server-side side-effect
   * dedupe lands with the first write tool in V2.4. Failures surface as
   * typed HTTP errors.
   */
  async executeTool(
    runId: string,
    runToken: string,
    toolName: string,
    call: { version: number; input: unknown; tool_call_id?: string },
  ): Promise<ToolExecutionResult> {
    const { json } = await this.request(
      "POST",
      `/internal/agent/runs/${encodeURIComponent(runId)}/tools/${encodeURIComponent(toolName)}/execute`,
      runToken,
      {
        version: call.version,
        input: call.input,
        ...(call.tool_call_id !== undefined ? { tool_call_id: call.tool_call_id } : {}),
      },
    );
    const body = json as { ok?: unknown; output?: unknown };
    if (body.ok !== true) {
      throw new InternalApiError(`tool execute returned unexpected body for ${toolName}`, 200, "http_error");
    }
    return { ok: true, result: body.output };
  }

  /**
   * POST /internal/agent/runs/{id}/settle. Only "succeeded"|"failed" may be
   * requested — "cancelled" is adjudicated by FastAPI. The error envelope is
   * split into error_code plus an error object carrying the message.
   */
  async settleRun(
    runId: string,
    runToken: string,
    outcome: "succeeded" | "failed",
    error?: { code: string; message: string },
  ): Promise<void> {
    await this.request(
      "POST",
      `/internal/agent/runs/${encodeURIComponent(runId)}/settle`,
      runToken,
      {
        status: outcome,
        ...(outcome === "failed" && error
          ? { error_code: error.code, error: { message: error.message } }
          : {}),
      },
    );
  }

  /** GET /api/health — used by the health endpoint probe only. */
  async probeFastAPI(): Promise<"reachable" | "unreachable"> {
    try {
      const response = await this.fetchImpl(`${this.config.apiBaseUrl}/api/health`, {
        method: "GET",
        signal: AbortSignal.timeout(2000),
      });
      return response.ok ? "reachable" : "unreachable";
    } catch {
      return "unreachable";
    }
  }
}
