/**
 * Sidecar worker loop.
 *
 * Per run (exactly one lease held at a time):
 *   lease → heartbeat(lease/3) → context → Pi session turn → events batch
 *   flush → settle(succeeded|failed).
 *
 * Crash semantics: any uncaught error settles the run failed with
 * SIDECAR_ERROR; a killed process leaves no local state — FastAPI's reaper
 * expires the lease and the durable queue re-drives recovery. Terminal runs
 * are never revived. V2.1 registers only read-only tools, so retries cannot
 * repeat side effects; server-side tool dedupe arrives with the first write
 * tool (V2.4) and must exist before any side-effectful tool is registered.
 */

import type { InternalClient, LeasedJob } from "./client.js";
import type { AgentConfig, AgentKind } from "./config.js";
import { RunCancelledError } from "./errors.js";
import { redactErrorText } from "./redact.js";
import { RunEventBuffer, extractText, type FgEvent } from "./events.js";
import type { Logger } from "./logger.js";
import { buildRunSession } from "./session.js";
import { peekRunTokenClaims } from "./tokens.js";
import { adapterFor } from "./adapters/kind.js";
import {
  RunRetryBudget,
  classifyProviderStreamError,
  resolveRetryBudgetLimits,
} from "./retry-budget.js";

export interface WorkerDeps {
  client: InternalClient;
  config: AgentConfig;
  logger: Logger;
  /** Test seam overriding buildRunSession. */
  sessionFactory?: typeof buildRunSession;
  /** Test seam overriding the monotonic clock used for stage timing. */
  now?: () => number;
}

interface PendingSlot {
  pending: true;
  kind: AgentKind;
}

interface ActiveRun {
  pending: false;
  kind: AgentKind;
  job: LeasedJob;
  heartbeatTimer: NodeJS.Timeout;
  abort: AbortController;
  leaseLost: boolean;
  /** Server-side cancel_requested observed via heartbeat; stop tool calls, skip settle. */
  cancelRequested: boolean;
  /**
   * First hard policy-block class for this run, or null while allowed.
   * Sticky for the lifetime of the run: SDK auto-retry must not launder it.
   */
  policyBlockCode: string | null;
  /** Settles (never rejects) when this slot is released. */
  done?: Promise<void>;
}

type Slot = PendingSlot | ActiveRun;

export class SidecarWorker {
  private readonly client: InternalClient;
  private readonly config: AgentConfig;
  private readonly logger: Logger;
  private readonly sessionFactory: typeof buildRunSession;
  /** Injectable monotonic clock (tests); defaults to process.hrtime-based. */
  private readonly now: () => number;
  /** Keyed by run_id for real runs; pending reservations use a synthetic key. */
  private readonly slots = new Map<string, Slot>();
  private slotSeq = 0;
  private stopped = false;

  constructor(deps: WorkerDeps) {
    this.client = deps.client;
    this.config = deps.config;
    this.logger = deps.logger;
    this.sessionFactory = deps.sessionFactory ?? buildRunSession;
    // Cancellation must not wait for the heartbeat cadence. The server answers
    // `cancel_requested` on *any* run-scoped request once the browser cancels
    // (append/tools/provider all fence on it), so observe it at the transport
    // and converge immediately. With the shipped 60s lease the heartbeat is
    // 20s apart — far longer than the in-flight append that discovers the
    // cancel — and the missed signal used to surface as a bogus SIDECAR_ERROR.
    this.client.onRunCancelled = (runId) => this.markCancelRequested(runId);
    // Monotonic: process.hrtime.bigint() cannot jump backwards on clock changes,
    // unlike Date.now(). Duration is what matters here, not wall-clock time.
    this.now = deps.now ?? (() => Number(process.hrtime.bigint() / 1_000_000n));
  }

  get isBusy(): boolean {
    return this.slots.size > 0;
  }

  /** Test seam: slots of this kind that are occupied (pending or running). */
  inFlight(kind: AgentKind): number {
    let count = 0;
    for (const slot of this.slots.values()) {
      if (slot.kind === kind) count += 1;
    }
    return count;
  }

  /** Concurrent slots for one kind. Budgets are independent: a long steward
   * call must not consume an assistant slot. Which config value applies is the
   * adapter's business, so this function has no kind branch. */
  private slotsFor(kind: AgentKind): number {
    return adapterFor(kind).slotBudget(this.config);
  }

  private enabledKinds(): AgentKind[] {
    switch (this.config.role) {
      case "assistant":
        return ["assistant"];
      case "steward":
        return ["steward"];
      default:
        return ["assistant", "steward"];
    }
  }

  /** Start polling; resolves immediately, loop runs in background. */
  start(): void {
    void this.pollLoop();
  }

  stop(): void {
    this.stopped = true;
  }

  private async pollLoop(): Promise<void> {
    while (!this.stopped) {
      let didWork = false;
      for (const kind of this.enabledKinds()) {
        // Fill every free slot of this kind. The awaits are sequential on
        // purpose: with the reservation below fanning out would be safe, but
        // serial leases keep an empty queue at one round trip per poll instead
        // of one per slot.
        while (!this.stopped && this.inFlight(kind) < this.slotsFor(kind)) {
          let leased = false;
          try {
            leased = (await this.leaseIntoSlot(kind)) !== null;
          } catch (error) {
            // Never let the poll loop die, and never spin on a failing
            // endpoint: a transient lease error just delays the next poll.
            this.logger.warn("poll loop iteration failed", {
              kind,
              error: error instanceof Error ? error.message : String(error),
            });
            break;
          }
          if (!leased) break;
          didWork = true;
        }
      }
      // A run that just finished means the queue may still hold more work, so
      // re-poll immediately. Sleeping first would add `leasePollIntervalMs` of
      // pure latency to every queued run after the first, which shows up as
      // queue_wait on the backend.
      if (didWork) continue;
      await this.sleep(this.config.leasePollIntervalMs);
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /**
   * Lease one job of `kind` into a slot and start running it, WITHOUT waiting
   * for the run to finish. Returns the slot, or null when there was nothing to
   * lease. This is the concurrency primitive: `pollLoop` uses it to fill every
   * free slot, so a long steward call cannot block an assistant slot.
   */
  async leaseIntoSlot(kind: AgentKind): Promise<ActiveRun | null> {
    if (!this.enabledKinds().includes(kind)) return null;
    if (this.inFlight(kind) >= this.slotsFor(kind)) return null;

    // Reserve BEFORE awaiting. `leaseJob` is a suspension point, so a
    // concurrent caller would otherwise see this slot as free and lease a
    // second job that no slot can run — that job would hold a live lease with
    // no executor until server-side recovery reclaimed it. (SQLite's write lock
    // guarantees two concurrent leases get *different* jobs, so this is exactly
    // the shape the reservation prevents.)
    const reservationKey = `pending:${kind}:${++this.slotSeq}`;
    this.slots.set(reservationKey, { pending: true, kind });

    let job: LeasedJob | null;
    try {
      job = await this.client.leaseJob(kind);
    } catch (error) {
      this.slots.delete(reservationKey); // Never leak a slot on failure.
      throw error;
    }
    if (job === null) {
      this.slots.delete(reservationKey);
      return null;
    }
    if (adapterFor(kind).adoptsServerConcurrency) {
      this.adoptServerConcurrency(job.max_concurrent);
    }

    const abort = new AbortController();
    const run: ActiveRun = {
      pending: false,
      kind,
      job,
      abort,
      leaseLost: false,
      cancelRequested: false,
      policyBlockCode: null,
      heartbeatTimer: this.startHeartbeat(job, abort.signal),
    };
    this.slots.delete(reservationKey);
    this.slots.set(job.run_id, run);

    // executeJob handles its own failures and never rejects; the catch is still
    // required — an unhandled rejection here would terminate the process.
    run.done = this.executeJob(job, run)
      .catch((error) => {
        this.logger.error("run lifecycle escaped its own error handling", {
          run_id: job.run_id,
          error: error instanceof Error ? error.message : String(error),
        });
      })
      .finally(() => {
        clearInterval(run.heartbeatTimer);
        // Remove only our own entry: a later run must not have its slot deleted
        // by an earlier one finishing.
        if (this.slots.get(job.run_id) === run) this.slots.delete(job.run_id);
      });
    return run;
  }

  /**
   * One lease attempt + full run lifecycle, awaiting completion.
   *
   * Exposed for tests, which drive a single serial run and assert on its
   * effects immediately afterwards. `pollLoop` deliberately does NOT use this
   * (it calls `leaseIntoSlot`) — awaiting here would serialise the slots and
   * undo the whole point of the multi-slot model.
   */
  async tryLeaseAndRun(kind: AgentKind = "assistant"): Promise<boolean> {
    const run = await this.leaseIntoSlot(kind);
    if (run === null) return false;
    await run.done;
    return true;
  }

  /**
   * Raise the steward slot budget to whatever the server says it will admit.
   *
   * The server decides how many attempts of one space may hold a lease; if the
   * local value is smaller, a leased attempt sits with no executor until recovery
   * reclaims it.
   * Only ever raised, never lowered, so the value cannot oscillate between
   * polls. This corrects a misconfiguration; it does not replace configuring
   * STEWARD_ASSIST_MAX_CONCURRENT_CALLS_PER_SPACE on both sides.
   */
  private adoptServerConcurrency(broadcast: number | undefined): void {
    if (broadcast === undefined) return;
    if (broadcast === this.config.stewardMaxConcurrentCallsPerSpace) return;
    if (broadcast > this.config.stewardMaxConcurrentCallsPerSpace) {
      this.logger.warn("steward concurrency raised to the server's limit", {
        local: this.config.stewardMaxConcurrentCallsPerSpace,
        server: broadcast,
      });
      this.config.stewardMaxConcurrentCallsPerSpace = broadcast;
    } else {
      this.logger.warn("steward concurrency below the server's limit", {
        local: this.config.stewardMaxConcurrentCallsPerSpace,
        server: broadcast,
      });
    }
  }

  private startHeartbeat(job: LeasedJob, signal?: AbortSignal): NodeJS.Timeout {
    // FastAPI does not advertise lease duration; it is sidecar config.
    const interval = Math.max(Math.floor(this.config.defaultLeaseMs / 3), 1000);
    return setInterval(() => {
      void this.client
        .heartbeat(job.job_id, job.run_token, signal)
        .then((result) => {
          if (!result.ok) {
            this.markLeaseLost(job.run_id);
          } else if (result.cancelRequested) {
            this.markCancelRequested(job.run_id);
          }
          // Adopt the reissued token. The run token has a hard TTL (600s) and is
          // only minted at lease time, so without this every run living longer
          // than 10 minutes would 401 on its next heartbeat and be aborted as a
          // lost lease. Every other request reads `job.run_token` at call time,
          // so updating the leased job in place is enough.
          if (result.runToken !== null) {
            job.run_token = result.runToken;
          }
        })
        .catch((error) => {
          // A cancellation verdict is not a lease loss: converge as cancelled so
          // the run is never settled failed by the sidecar.
          if (error instanceof RunCancelledError) {
            this.markCancelRequested(job.run_id);
            return;
          }
          const status =
            error instanceof Error && "status" in error
              ? (error as { status?: number }).status
              : undefined;
          // A terminal auth/scope response means this lease can no longer be
          // trusted (membership revocation is 403; cancelled/settled runs may
          // be 409; expiry is 410). Abort immediately instead of allowing the
          // in-flight Pi loop to continue until its next internal request.
          if (status !== undefined && [401, 403, 409, 410].includes(status)) {
            this.markLeaseLost(job.run_id);
          }
        });
    }, interval);
  }

  private markLeaseLost(runId: string): void {
    const slot = this.slots.get(runId);
    // Look up by run_id: with several slots in flight, comparing against a
    // single "current" run would silently ignore every other slot's signal.
    if (slot !== undefined && !slot.pending && !slot.leaseLost) {
      slot.leaseLost = true;
      slot.abort.abort();
      this.logger.warn("lease lost, aborting run", { run_id: runId });
    }
  }

  private markCancelRequested(runId: string): void {
    const slot = this.slots.get(runId);
    if (slot !== undefined && !slot.pending && !slot.cancelRequested) {
      slot.cancelRequested = true;
      slot.abort.abort();
      // Cancellation is adjudicated server-side; the sidecar stops issuing
      // tool calls and skips settling (never settles "cancelled" itself).
      this.logger.warn("cancel requested by server, stopping tool calls", { run_id: runId });
    }
  }

  /**
   * Records the first hard policy-block decision for this run and stops it.
   *
   * Called synchronously from the guard the moment it blocks, so the run stops
   * as soon as the decision exists rather than when the model loop happens to
   * end. The code is sticky: SDK auto-retry re-invokes the guard, which refuses
   * while blocked, so no further provider request or tool call is issued.
   */
  private markPolicyBlocked(runId: string, code: string): void {
    const slot = this.slots.get(runId);
    if (slot === undefined || slot.pending) return;
    if (slot.policyBlockCode === null) {
      slot.policyBlockCode = code;
      this.logger.warn("policy guard blocked run; stopping model and tool calls", {
        run_id: runId,
        error_code: code,
      });
    }
    slot.abort.abort();
  }

  private async executeJob(job: LeasedJob, active: ActiveRun): Promise<void> {
    const log = this.logger.child({ run_id: job.run_id });
    const adapter = adapterFor(active.kind);
    try {
      // Stage origin for the sidecar's preparation phase: everything from
      // receiving the lease to SDK agent_start (context fetch + session
      // creation). run.started is emitted by the SDK, so without this origin the
      // backend would have to misattribute that time to queue wait.
      const prepStartedAt = this.now();
      const projection = await this.client.getRunContext(job.run_id, job.run_token, active.abort.signal);
      if (projection.agent_kind !== active.kind || job.agent_kind !== active.kind) {
        throw new Error(
          `sidecar received a ${job.agent_kind} job on a ${active.kind} slot`,
        );
      }
      adapter.verifyProjection(projection);
      if (projection.run_id !== job.run_id || projection.attempt !== job.attempt) {
        throw new Error("context belongs to a different run attempt");
      }
      const events = new RunEventBuffer(projection.next_event_seq, projection.context_build_id === null
        ? undefined : { build_id: projection.context_build_id, attempt: projection.attempt,
          allowed_handles: (projection.context_blocks ?? []).map((block) => block.citation) },
        { prepStartedAt, now: this.now },
        adapter.publishesConversation);
      if (projection.cancel_requested) {
        active.cancelRequested = true;
        active.abort.abort();
        return;
      }

      // Explainable refusal: provider policy resolved server-side. The model
      // loop never starts; the reason is appended as a registry-legal
      // run.failed event, then settled failed with PROVIDER_<POLICY_RESULT>.
      const policyResult = projection.provider?.policy_result;
      if (policyResult !== "allowed") {
        await this.refuseOnProviderPolicy(job, events, policyResult);
        return;
      }

      // One budget per run: it must never be shared with another run, and the
      // two retry layers (request + session) are only bounded together here.
      const retryLimits = resolveRetryBudgetLimits(this.config);
      const retryBudget = retryLimits === null ? null : new RunRetryBudget(retryLimits);

      // Getter, not the token: the heartbeat renews it, and a captured value
      // would keep using the expired one (hard TTL) on long runs.
      const bundle = await this.sessionFactory(this.config, this.client, projection, () => job.run_token, {
        shouldStopToolCalls: () =>
          active.cancelRequested || active.leaseLost || active.policyBlockCode !== null,
        onPolicyBlock: (code) => this.markPolicyBlocked(job.run_id, code),
        signal: active.abort.signal,
        retryBudget: retryBudget ?? undefined,
      });
      const { session } = bundle;
      const abortSession = () => {
        void session.abort().catch((error: unknown) => {
          this.logger.warn("failed to abort Pi session", {
            run_id: job.run_id,
            error: error instanceof Error ? error.message : String(error),
          });
        });
      };
      if (active.abort.signal.aborted) abortSession();
      else active.abort.signal.addEventListener("abort", abortSession, { once: true });

      const lastAssistantError: {
        current: { stopReason: string; errorMessage?: string } | null;
      } = { current: null };
      // Text of the last completed assistant message: the run's final answer.
      // Kept separate from the error tracker so neither branch changes the
      // other's reset conditions.
      const lastAssistantText: { current: string | null } = { current: null };
      session.subscribe((event) => {
        const raw = event as {
          type?: string;
          message?: {
            role?: string;
            stopReason?: string;
            errorMessage?: string;
            content?: unknown;
          };
        };
        // Pi can compact and retry within one prompt(). Keep a provider failure
        // unresolved until a later assistant reply fully completes; partial
        // responses, tool turns and compaction events do not supersede it.
        if (
          raw.type === "message_end" &&
          raw.message?.role === "assistant" &&
          raw.message.stopReason === "error"
        ) {
          lastAssistantError.current = {
            stopReason: "error",
            errorMessage:
              typeof raw.message.errorMessage === "string"
                ? raw.message.errorMessage
                : undefined,
          };
        } else if (
          raw.type === "message_end" &&
          raw.message?.role === "assistant" &&
          raw.message.stopReason === "stop"
        ) {
          lastAssistantError.current = null;
        }
        if (raw.type === "error") {
          lastAssistantError.current = {
            stopReason: "error",
            errorMessage:
              typeof (raw as { error?: { errorMessage?: unknown } }).error?.errorMessage === "string"
                ? String((raw as { error?: { errorMessage?: unknown } }).error?.errorMessage)
                : "provider stream ended in error",
          };
        }
        // The final answer is the LAST generation that completed: stop (full
        // answer) or length (truncated, still has prose). toolUse is not an
        // answer and is superseded by the turn that follows it; error is owned
        // by the branch above; aborted is adjudicated server-side. Later
        // messages overwrite earlier ones, so a prose tool turn followed by an
        // empty stop message correctly leaves the run without an answer.
        if (
          raw.type === "message_end" &&
          raw.message?.role === "assistant" &&
          (raw.message.stopReason === "stop" || raw.message.stopReason === "length")
        ) {
          lastAssistantText.current = extractText(raw.message.content);
        }
        events.onSessionEvent(event as { type: string });
      });

      // User prompts are projected from context messages: role +
      // content_json["text"] only; entries without text are skipped.
      const userMessage = [...projection.messages].reverse().find(
        (m) => m.role === "user" && typeof m.content_json["text"] === "string",
      );
      const promptText =
        typeof userMessage?.content_json["text"] === "string"
          ? userMessage.content_json["text"]
          : "";
      // The prompt body is the adapter's business: an assistant run appends its
      // retrieved context with citation handles, a steward run's projection IS the
      // whole input and must not gain an appendix the server never sent.
      const modelPrompt = adapter.modelPrompt(projection, promptText);
      // message.user_added is backend-owned (written once at enqueue, seq 0) and
      // already present in projection.messages; the sidecar only consumes it.

      // Batched event flushing while the model loop runs.
      // Pass a getter, not the token: the heartbeat renews the run token, and a
      // captured value would keep sending the expired one (the token has a hard
      // TTL, so a long run's appends would 401 after ~10 minutes).
      const flusher = this.startEventFlusher(
        job.run_id,
        () => job.run_token,
        events,
        active.abort.signal,
      );

      try {
        await session.prompt(modelPrompt, { source: "rpc", expandPromptTemplates: false });
      } finally {
        await flusher.flushAll();
      }

      // FastAPI owns expired/cancel-requested runs; never settle them here.
      if (active.leaseLost || active.cancelRequested) return;

      // A hard policy block is reported with the class the guard actually
      // detected. The check reads the sticky slot state as well as the guard,
      // because the decision may have arrived through `onPolicyBlock` before
      // this point and must not be lost if the guard instance is replaced.
      const policyBlockCode = active.policyBlockCode ?? bundle.policyGuard.blockCode;
      if (policyBlockCode !== null) {
        // Terminal event is backend-owned: /settle writes run.failed with the
        // error code below. The sidecar only flushes any pending turn events.
        await this.flushEvents(job.run_id, job.run_token, events.drain(), active.abort.signal);
        await this.client.settleRun(job.run_id, job.run_token, "failed", {
          code: policyBlockCode,
          message: "policy guard blocked activity during this run",
        });
        log.warn("run settled failed: policy violation", {
          error_code: policyBlockCode,
          violations: bundle.policyGuard.blockingViolationCount,
          policy_incidents: bundle.policyGuard.incidents.length,
        });
        return;
      }
      if (lastAssistantError.current !== null) {
        // Provider returned an errored assistant message (no usable answer).
        // Do not settle succeeded: surface the redacted provider error.
        // Upstream error text is redacted before any durable sink (settle/log).
        const message = redactErrorText(
          lastAssistantError.current.errorMessage ?? "provider stream ended in error",
        );
        // A budget stop is not an upstream failure: reporting it as
        // PROVIDER_STREAM_ERROR would blame the provider and hide that the
        // sidecar deliberately stopped retrying. See retry-budget.ts for why
        // both the sentinel message AND a genuinely exhausted budget are needed.
        const code = classifyProviderStreamError({
          message,
          budgetExhausted: retryBudget !== null && retryBudget.snapshot().exhausted,
        });
        await this.flushEvents(job.run_id, job.run_token, events.drain(), active.abort.signal);
        await this.client.settleRun(job.run_id, job.run_token, "failed", { code, message });
        log.warn("run settled failed: provider stream error", {
          error_code: code,
          message,
          ...(code === "PROVIDER_RETRY_BUDGET_EXHAUSTED" && retryBudget !== null
            ? { retry_budget: retryBudget.snapshot() }
            : {}),
        });
        return;
      }
      // No usable answer: the model completed its turn without producing any
      // prose. Settling succeeded would leave the user with neither an answer
      // nor an explanation. This applies to both kinds: an empty product is
      // equally unusable, and for the steward the server would otherwise assert
      // on a missing text and fail the run for a reason the sidecar caused.
      const product = adapter.extractProduct(lastAssistantText.current);
      if (product === null || product.length === 0) {
        const message = adapter.reportsProductOnSettle
          ? "model completed the run without returning a product"
          : "model completed the run without returning any answer text";
        await this.flushEvents(job.run_id, job.run_token, events.drain(), active.abort.signal);
        await this.client.settleRun(job.run_id, job.run_token, "failed", {
          code: "PROVIDER_EMPTY_ANSWER",
          message,
        });
        log.warn("run settled failed: empty model output", { message });
        return;
      }
      // Terminal event (run.settled) is written by the backend /settle handler;
      // the sidecar must not emit a duplicate. The product travels with the
      // settlement for kinds whose output cannot go through message events
      // (child runs refuse them), so it must be attached here or it is lost.
      await this.flushEvents(job.run_id, job.run_token, events.drain(), active.abort.signal);
      await this.client.settleRun(
        job.run_id,
        job.run_token,
        "succeeded",
        undefined,
        adapter.reportsProductOnSettle && product !== null ? { output_text: product } : undefined,
      );
      log.info("run settled succeeded");
    } catch (error) {
      // Cancellation/lease loss is adjudicated by FastAPI.  The abort signal
      // intentionally rejects the in-flight Pi/internal request; do not turn
      // that expected rejection into a sidecar ``failed`` settle that could
      // race the server's cancelled terminal state.
      //
      // ``RunCancelledError`` is the server's explicit cancellation verdict on
      // an internal write (409 + detail.reason=cancel_requested). It reaches
      // here before the heartbeat can, so it must be honoured on its own —
      // relying only on the flags let the catch-all below settle the run
      // ``failed`` with SIDECAR_ERROR, hiding the user's own cancellation.
      if (error instanceof RunCancelledError) {
        this.markCancelRequested(job.run_id);
        return;
      }
      if (active.cancelRequested || active.leaseLost) return;
      // A policy block that aborted the session surfaces here as a stream/abort
      // error. Report the block class, never SIDECAR_ERROR: the cause is known
      // and must stay explainable.
      if (active.policyBlockCode !== null) {
        const blockCode = active.policyBlockCode;
        await this.client
          .settleRun(job.run_id, job.run_token, "failed", {
            code: blockCode,
            message: "policy guard blocked activity during this run",
          })
          .catch(() => undefined);
        log.warn("run settled failed: policy violation", { error_code: blockCode });
        return;
      }
      const rawErrorCode =
        error instanceof Error && "errorCode" in error
          ? String((error as { errorCode: unknown }).errorCode)
          : "SIDECAR_ERROR";
      const errorCode = rawErrorCode;
      const message = redactErrorText(error instanceof Error ? error.message : String(error));
      // Never include secret material in error payloads (redactErrorText enforces).
      log.error("run failed", { error_code: errorCode, message });
      try {
        const token = job.run_token;
        await this.client.settleRun(job.run_id, token, "failed", {
          code: errorCode,
          message,
        }).catch(() => undefined);
      } catch {
        /* settle failure is recovered by FastAPI's reaper */
      }
    }
  }

  /**
   * Explainable provider-policy refusal: append a registry-legal run.failed
   * event carrying the reason, then settle failed with PROVIDER_<RESULT>.
   * No model loop is started and no tool is called.
   */
  private async refuseOnProviderPolicy(
    job: LeasedJob,
    events: RunEventBuffer,
    policyResult: string | undefined,
  ): Promise<void> {
    const errorCode =
      policyResult === undefined ? "PROVIDER_UNRESOLVED" : `PROVIDER_${policyResult.toUpperCase()}`;
    const message =
      policyResult === undefined
        ? "provider resolution unavailable for this run"
        : `provider policy refuses this run (${policyResult})`;
    // Backend /settle writes the run.failed terminal event with error_code.
    await this.flushEvents(job.run_id, job.run_token, events.drain());
    await this.client.settleRun(job.run_id, job.run_token, "failed", {
      code: errorCode,
      message,
    });
    this.logger.warn("run refused by provider policy", {
      run_id: job.run_id,
      error_code: errorCode,
    });
  }

  private startEventFlusher(
    runId: string,
    currentRunToken: () => string,
    events: { drain(): FgEvent[] },
    signal?: AbortSignal,
  ): { flushAll(): Promise<void> } {
    // Serialized send queue: batches reach FastAPI strictly in seq order.
    // Keep one pump promise rather than chaining independent promises: when a
    // batch fails, every later batch must remain behind it (never be retried
    // out of order or silently detached from the queue).
    const pending: FgEvent[] = [];
    let pumpPromise: Promise<void> | null = null;
    const pump = (): Promise<void> => {
      if (pumpPromise !== null) return pumpPromise;
      pumpPromise = (async () => {
        while (pending.length > 0) {
          const batch = pending.splice(0, this.config.eventFlushBatchSize);
          try {
            await this.flushBuffered(runId, currentRunToken(), batch, signal);
          } catch (error) {
            // Reinsert at the head so a retry preserves strict sequence order;
            // later batches remain queued behind this failed batch.
            pending.unshift(...batch);
            throw error;
          }
        }
      })().finally(() => {
        pumpPromise = null;
      });
      return pumpPromise;
    };
    const timer = setInterval(() => {
      pending.push(...events.drain());
      void pump().catch(() => undefined);
    }, this.config.eventFlushIntervalMs);

    const flushAll = async (): Promise<void> => {
      clearInterval(timer);
      pending.push(...events.drain());
      let retried = false;
      while (pending.length > 0 || pumpPromise !== null) {
        try {
          await pump();
        } catch (error) {
          if (retried) throw error;
          // One immediate retry covers a transient append failure; a
          // persistent failure remains visible and is recovered by the
          // server-side reaper rather than being reported as success.
          retried = true;
        }
      }
    };
    return { flushAll };
  }

  private async flushBuffered(
    runId: string,
    runToken: string,
    buffer: FgEvent[],
    signal?: AbortSignal,
  ): Promise<void> {
    if (buffer.length === 0) return;
    // ``buffer`` has already been detached from the pending queue by
    // enqueueBatch.  Keep it intact until appendEvents succeeds; otherwise a
    // transient 5xx would silently drop this event batch before settle/reaper
    // can recover the run.
    const batch = buffer;
    const { duplicates } = await this.client.appendEvents(runId, runToken, batch, signal);
    // Duplicates are success: at-least-once delivery, exactly-once stream.
    if (duplicates.length > 0) {
      this.logger.debug("duplicate events accepted idempotently", {
        run_id: runId,
        duplicates: duplicates.length,
      });
    }
  }

  private flushEvents(
    runId: string,
    runToken: string,
    batch: FgEvent[],
    signal?: AbortSignal,
  ): Promise<void> {
    if (batch.length === 0) return Promise.resolve();
    return this.client.appendEvents(runId, runToken, batch, signal).then(() => undefined);
  }
}

/** Diagnostics helper: unverified peek at a run token's claims (never for authorization). */
export function describeRunTokenScope(runToken: string): Record<string, unknown> {
  const claims = peekRunTokenClaims(runToken);
  return {
    run_id: claims?.["run_id"],
    agent_kind: claims?.["agent_kind"],
  };
}
