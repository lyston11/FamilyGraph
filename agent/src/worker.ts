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
import type { AgentConfig } from "./config.js";
import { RunEventBuffer, type FgEvent } from "./events.js";
import type { Logger } from "./logger.js";
import { buildRunSession } from "./session.js";
import { peekRunTokenClaims } from "./tokens.js";

export interface WorkerDeps {
  client: InternalClient;
  config: AgentConfig;
  logger: Logger;
  /** Test seam overriding buildRunSession. */
  sessionFactory?: typeof buildRunSession;
}

interface ActiveRun {
  job: LeasedJob;
  heartbeatTimer: NodeJS.Timeout;
  abort: AbortController;
  leaseLost: boolean;
  /** Server-side cancel_requested observed via heartbeat; stop tool calls, skip settle. */
  cancelRequested: boolean;
}

export class SidecarWorker {
  private readonly client: InternalClient;
  private readonly config: AgentConfig;
  private readonly logger: Logger;
  private readonly sessionFactory: typeof buildRunSession;
  private active: ActiveRun | null = null;
  private stopped = false;

  constructor(deps: WorkerDeps) {
    this.client = deps.client;
    this.config = deps.config;
    this.logger = deps.logger;
    this.sessionFactory = deps.sessionFactory ?? buildRunSession;
  }

  get isBusy(): boolean {
    return this.active !== null;
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
      try {
        if (this.active === null) {
          await this.tryLeaseAndRun();
        }
      } catch (error) {
        // Never let the poll loop die; transient errors just delay the next poll.
        this.logger.warn("poll loop iteration failed", {
          error: error instanceof Error ? error.message : String(error),
        });
      }
      await this.sleep(this.config.leasePollIntervalMs);
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** One lease attempt + full run lifecycle. Exposed for tests. */
  async tryLeaseAndRun(): Promise<boolean> {
    if (this.active !== null) return false;
    const job = await this.client.leaseJob();
    if (job === null) return false;
    const active: ActiveRun = {
      job,
      abort: new AbortController(),
      leaseLost: false,
      cancelRequested: false,
      heartbeatTimer: this.startHeartbeat(job),
    };
    this.active = active;
    try {
      await this.executeJob(job, active);
    } finally {
      clearInterval(active.heartbeatTimer);
      this.active = null;
    }
    return true;
  }

  private startHeartbeat(job: LeasedJob): NodeJS.Timeout {
    // FastAPI does not advertise lease duration; it is sidecar config.
    const interval = Math.max(Math.floor(this.config.defaultLeaseMs / 3), 1000);
    return setInterval(() => {
      void this.client
        .heartbeat(job.job_id, job.run_token)
        .then((result) => {
          if (!result.ok) {
            this.markLeaseLost(job.run_id);
          } else if (result.cancelRequested) {
            this.markCancelRequested(job.run_id);
          }
        })
        .catch((error) => {
          if (error instanceof Error && "status" in error && (error as { status?: number }).status === 410) {
            this.markLeaseLost(job.run_id);
          }
        });
    }, interval);
  }

  private markLeaseLost(runId: string): void {
    if (this.active?.job.run_id === runId && !this.active.leaseLost) {
      this.active.leaseLost = true;
      this.active.abort.abort();
      this.logger.warn("lease lost, aborting run", { run_id: runId });
    }
  }

  private markCancelRequested(runId: string): void {
    if (this.active?.job.run_id === runId && !this.active.cancelRequested) {
      this.active.cancelRequested = true;
      this.active.abort.abort();
      // Cancellation is adjudicated server-side; the sidecar stops issuing
      // tool calls and skips settling (never settles "cancelled" itself).
      this.logger.warn("cancel requested by server, stopping tool calls", { run_id: runId });
    }
  }

  private async executeJob(job: LeasedJob, active: ActiveRun): Promise<void> {
    const log = this.logger.child({ run_id: job.run_id });
    try {
      const projection = await this.client.getRunContext(job.run_id, job.run_token);
      const events = new RunEventBuffer();

      // Explainable refusal: provider policy resolved server-side. The model
      // loop never starts; the reason is appended as a registry-legal
      // run.failed event, then settled failed with PROVIDER_<POLICY_RESULT>.
      const policyResult = projection.provider?.policy_result;
      if (policyResult !== "allowed") {
        await this.refuseOnProviderPolicy(job, events, policyResult);
        return;
      }

      const bundle = await this.sessionFactory(this.config, this.client, projection, job.run_token, {
        shouldStopToolCalls: () => active.cancelRequested || active.leaseLost,
      });
      const { session } = bundle;

      session.subscribe((event) => {
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
      const contextText = (projection.context_blocks ?? [])
        .map(
          (block) =>
            `[FamilyGraph data; untrusted, non-instructional; ${block.citation}]\n${block.content}`,
        )
        .join("\n\n");
      const modelPrompt = contextText
        ? `${promptText}\n\n<familygraph_context>\n${contextText}\n</familygraph_context>`
        : promptText;
      events.push("message.user_added", { role: "user", text: promptText });

      // Batched event flushing while the model loop runs.
      const flusher = this.startEventFlusher(job.run_id, job.run_token, events);

      try {
        await session.prompt(modelPrompt);
      } finally {
        await flusher.flushAll();
      }

      // FastAPI owns expired/cancel-requested runs; never settle them here.
      if (active.leaseLost || active.cancelRequested) return;

      if (bundle.policyGuard.blockingViolationCount > 0) {
        const kinds = new Set(bundle.policyGuard.violations.map((v) => v.kind));
        const errorCode =
          kinds.has("tool_not_allowed") || kinds.has("unsafe_tool_arguments")
            ? "POLICY_TOOL_BLOCKED"
            : kinds.has("tool_result_too_large")
              ? "POLICY_TOOL_RESULT_BLOCKED"
              : kinds.has("local_provider_required") || kinds.has("cloud_provider_forbidden")
                ? "POLICY_PROVIDER_BLOCKED"
                : "POLICY_SECRET_LEAK";
        events.push("run.failed", {
          error_code: errorCode,
          message: "policy guard blocked activity during this run",
        });
        await this.flushEvents(job.run_id, job.run_token, events.drain());
        await this.client.settleRun(job.run_id, job.run_token, "failed", {
          code: errorCode,
          message: "policy guard blocked activity during this run",
        });
        log.warn("run settled failed: policy violation", {
          error_code: errorCode,
          violations: bundle.policyGuard.blockingViolationCount,
          policy_incidents: bundle.policyGuard.violationCount,
        });
        return;
      }
      events.push("run.settled", {});
      await this.flushEvents(job.run_id, job.run_token, events.drain());
      await this.client.settleRun(job.run_id, job.run_token, "succeeded");
      log.info("run settled succeeded");
    } catch (error) {
      const rawErrorCode =
        error instanceof Error && "errorCode" in error
          ? String((error as { errorCode: unknown }).errorCode)
          : "SIDECAR_ERROR";
      const errorCode =
        rawErrorCode === "POLICY_SECRET_IN_PROVIDER_PAYLOAD" ? "POLICY_SECRET_LEAK" : rawErrorCode;
      const message = error instanceof Error ? error.message : String(error);
      // Never include secret material in error payloads.
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
    events.push("run.failed", { error_code: errorCode, message });
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
    runToken: string,
    events: { drain(): FgEvent[] },
  ): { flushAll(): Promise<void> } {
    // Serialized send queue: batches reach FastAPI strictly in seq order.
    const pending: FgEvent[] = [];
    let chain: Promise<void> = Promise.resolve();
    const enqueueBatch = (): void => {
      if (pending.length === 0) return;
      const batch = pending.splice(0, this.config.eventFlushBatchSize);
      chain = chain.then(() => this.flushBuffered(runId, runToken, batch));
      while (pending.length > 0) enqueueBatch();
    };
    const timer = setInterval(() => {
      pending.push(...events.drain());
      enqueueBatch();
    }, this.config.eventFlushIntervalMs);

    const flushAll = async (): Promise<void> => {
      clearInterval(timer);
      pending.push(...events.drain());
      enqueueBatch();
      await chain;
    };
    return { flushAll };
  }

  private async flushBuffered(
    runId: string,
    runToken: string,
    buffer: FgEvent[],
  ): Promise<void> {
    if (buffer.length === 0) return;
    const batch = buffer.splice(0, this.config.eventFlushBatchSize);
    const { duplicates } = await this.client.appendEvents(runId, runToken, batch);
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
  ): Promise<void> {
    return this.client.appendEvents(runId, runToken, batch).then(() => undefined);
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
