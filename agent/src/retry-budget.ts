/**
 * Run-level retry budget.
 *
 * WHY this exists: the provider request layer and the Pi session layer retry
 * independently, so their budgets multiply. With the shipped values that is
 * `(5+1) x (3+1) = 24` real outbound attempts for one transient failure. A
 * single upstream outage (observed 2026-10-01: Tailscale DERP dropped the
 * route to the provider peer) then turns one 10s connect timeout into ~4
 * minutes of pointless work per run and floods the egress audit.
 *
 * The two layers are configured in different places (pi-ai request options vs
 * Pi `settings.retry`), and neither can see the other's consumption. The only
 * point both layers funnel through is the HTTP transport itself, so the budget
 * is enforced there: every real attempt calls `fetch`, and once the budget is
 * exhausted `fetch` throws an error that BOTH layers decline to retry.
 *
 * Why the error text matters (verified against pi-ai 0.84.3 /
 * pi-coding-agent 0.84.3):
 *  - Layer 1 (`retryProviderRequest`) only retries errors carrying `status` and
 *    `headers`; a plain `Error` is not a provider error and is rethrown as-is.
 *  - Layer 2 (`isRetryableAssistantError`) matches the error message against
 *    `RETRYABLE_PROVIDER_ERROR_PATTERN` (overloaded / rate limit / 429 / 5xx /
 *    timeout / connection error / ...). `RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE`
 *    matches none of those, and matches no context-overflow pattern either, so
 *    it is neither retried nor mistaken for compaction.
 *
 * Changing the message text is therefore a behavior change, not a cosmetic one.
 */

/** Message deliberately outside both retryable and overflow patterns. */
export const RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE = "FamilyGraph run retry budget exhausted";

export interface RunRetryBudgetLimits {
  /** Maximum real provider HTTP attempts for the whole run. */
  maxProviderAttempts: number;
  /** Maximum wall-clock spent on provider attempts before the run gives up. */
  maxTotalMs: number;
}

export interface RunRetryBudgetSnapshot {
  providerAttempts: number;
  maxProviderAttempts: number;
  elapsedMs: number;
  maxTotalMs: number;
  exhausted: boolean;
  /** Why it stopped: `attempts`, `elapsed`, or null while the budget holds. */
  exhaustedBy: "attempts" | "elapsed" | null;
}

export class RunRetryBudgetExhaustedError extends Error {
  constructor(
    readonly snapshot: RunRetryBudgetSnapshot,
    readonly reason: "attempts" | "elapsed",
  ) {
    super(RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE);
    this.name = "RunRetryBudgetExhaustedError";
  }
}

/**
 * One instance per run. Not shared, not process-global: a run must never
 * consume another run's budget, and a per-tenant limit belongs to the
 * scheduler, not here.
 */
export class RunRetryBudget {
  private attempts = 0;
  private readonly startedAt: number;

  constructor(
    private readonly limits: RunRetryBudgetLimits,
    private readonly now: () => number = () => Date.now(),
  ) {
    this.startedAt = this.now();
  }

  get providerAttempts(): number {
    return this.attempts;
  }

  snapshot(): RunRetryBudgetSnapshot {
    const elapsedMs = this.now() - this.startedAt;
    const exhaustedBy = this.exhaustedBy();
    return {
      providerAttempts: this.attempts,
      maxProviderAttempts: this.limits.maxProviderAttempts,
      elapsedMs,
      maxTotalMs: this.limits.maxTotalMs,
      exhausted: exhaustedBy !== null,
      exhaustedBy,
    };
  }

  private exhaustedBy(): "attempts" | "elapsed" | null {
    if (this.attempts >= this.limits.maxProviderAttempts) return "attempts";
    if (this.now() - this.startedAt >= this.limits.maxTotalMs) return "elapsed";
    return null;
  }

  /**
   * Wrap the transport so every real provider attempt is charged to this run.
   *
   * Returns a fetch-shaped function; the caller decides whether to install it.
   *
   * Refusal shape matters and is NOT a free choice. Throwing an `Error` looks
   * correct in isolation, but the OpenAI SDK wraps a transport throw into its
   * own `APIConnectionError("Connection error.")`, whose text matches the SDK's
   * transient pattern — so both layers keep retrying (they just no longer reach
   * a socket) and the run still burns ~60s of backoff before failing. Measured:
   * a throwing refusal kept the gateway at the correct attempt count while the
   * run took 30s+ in pure backoff.
   *
   * Answering with a synthetic permanent 4xx instead mirrors exactly what the
   * gateway already does for a permanent upstream rejection, and both layers
   * decline it for the same documented reasons:
   *  - layer 1 only retries 408/409/429/5xx;
   *  - layer 2 matches the error text against the transient pattern, and the
   *    sentinel message matches none of it.
   * No socket is opened, so a refusal never becomes traffic.
   */
  wrapFetch(inner: typeof globalThis.fetch): typeof globalThis.fetch {
    // Typed from the platform fetch itself so this module needs no DOM lib.
    return (async (...args: Parameters<typeof globalThis.fetch>) => {
      const reason = this.exhaustedBy();
      if (reason !== null) {
        // Refuse BEFORE the socket: a budget rejection must not itself be an
        // outbound attempt, or "budget exceeded" would keep generating traffic.
        return budgetExhaustedResponse(this.snapshot(), reason);
      }
      this.attempts += 1;
      return inner(...args);
    }) as typeof globalThis.fetch;
  }
}

/** Resolve the per-run limits from config; both must be positive to be active. */
export function resolveRetryBudgetLimits(config: {
  runMaxProviderAttempts: number;
  runMaxTotalRetryMs: number;
}): RunRetryBudgetLimits | null {
  if (config.runMaxProviderAttempts <= 0 || config.runMaxTotalRetryMs <= 0) return null;
  return {
    maxProviderAttempts: config.runMaxProviderAttempts,
    maxTotalMs: config.runMaxTotalRetryMs,
  };
}

/** HTTP status used for the refusal: 4xx is never retried by either layer. */
export const RUN_RETRY_BUDGET_HTTP_STATUS = 400;

/**
 * Synthetic permanent-rejection response used when the budget is exhausted.
 *
 * Shaped like the gateway's own redacted error envelope so the sidecar's error
 * handling sees a familiar, non-retryable permanent rejection rather than a
 * transport fault. The sentinel message is what the worker keys on to report
 * PROVIDER_RETRY_BUDGET_EXHAUSTED instead of blaming the upstream.
 */
export function budgetExhaustedResponse(
  snapshot: RunRetryBudgetSnapshot,
  reason: "attempts" | "elapsed",
): Response {
  return new Response(
    JSON.stringify({
      error: {
        code: "RUN_RETRY_BUDGET_EXHAUSTED",
        message: RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE,
      },
      budget: snapshot,
      reason,
    }),
    {
      status: RUN_RETRY_BUDGET_HTTP_STATUS,
      headers: { "content-type": "application/json" },
    },
  );
}

/**
 * Classify a provider stream failure for the run's settle error code.
 *
 * Requires BOTH signals on purpose:
 *  - the message carries the budget sentinel, and
 *  - the budget is actually exhausted.
 *
 * Either alone is insufficient. Message-only would trust error text (an upstream
 * body is redacted, not verified, so it could contain the sentinel); budget-only
 * would blame the budget for an unrelated failure that happened to occur after
 * the last allowed attempt (e.g. an unparseable product). Together they say
 * "the run stopped because its budget ran out".
 */
export function classifyProviderStreamError(input: {
  message: string;
  budgetExhausted: boolean;
}): "PROVIDER_RETRY_BUDGET_EXHAUSTED" | "PROVIDER_STREAM_ERROR" {
  return input.message.includes(RUN_RETRY_BUDGET_EXHAUSTED_MESSAGE) && input.budgetExhausted
    ? "PROVIDER_RETRY_BUDGET_EXHAUSTED"
    : "PROVIDER_STREAM_ERROR";
}
