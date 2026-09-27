/**
 * Per-kind adapter: the one place that knows how the two runtime kinds differ.
 *
 * Why this exists. `executeJob` and `buildRunSession` describe a *run lifecycle*
 * (lease → context → session → prompt → events → settle), which is identical for
 * both kinds, plus a handful of *conversation semantics*, which are not: which
 * system prompt to load, which tools may be registered, what the cache key is
 * derived from, whether the product travels through message events or through
 * settlement. Those were interleaved as `if (kind === "steward")` branches inside
 * both functions, so every new kind difference had to be threaded through the
 * lifecycle code and the two concerns drifted together.
 *
 * Now the lifecycle depends only on this interface, and a kind difference is a
 * member added here. The set is closed and total: every branch that used to test
 * the kind is represented below, which is what makes "no `if kind` left in the
 * worker" a property that can be asserted rather than a claim.
 *
 * This module must stay free of I/O and of mutable state — the adapters are
 * frozen singletons, so a bug cannot leak from one run into the next.
 */

import type { LeasedJob, RunContextProjection } from "../client.js";
import { InternalApiError } from "../errors.js";
import { renderContextAppendix } from "../context.js";
import type { AgentConfig, AgentKind } from "../config.js";
import { ASSISTANT_SYSTEM_PROMPT } from "../prompt.js";
import { STEWARD_PROMPT_VERSION } from "../prompts/steward.js";
import { toolNamesFor } from "../tools.js";

/** The request that asks the backend for one lease of this kind. */
export interface LeaseRequestSpec {
  path: string;
  body: Record<string, unknown>;
}

export interface KindAdapter {
  readonly kind: AgentKind;

  /** System prompt for this kind, given the projection.
   *
   * A function rather than a constant because the steward's per-kind instructions
   * are server-owned: the in-process carrier sends them as the system message, and
   * ``prompt_digest`` is computed over them. A child run that sent a different
   * system message would ask the model a different question than the digest
   * claims — and for the candidate kind the difference is load-bearing (the
   * direction semantics and conflict rules live in that text). */
  systemPrompt(projection: RunContextProjection): string;

  /** The prompt body handed to the model, from the projection.
   *
   * Differs by kind because the projection means different things: an assistant
   * run carries conversation plus retrieved context (which is appended with
   * citation handles), while a steward run carries one structured projection that
   * IS the whole input. */
  modelPrompt(projection: RunContextProjection, userText: string): string;

  /** Whether an empty tool allowlist is a protocol error for this kind.
   *  The assistant always carries read-only query tools; the steward's set is
   *  empty by design (its output is a structured product, not tool calls), so
   *  only the membership half of the allowlist check applies to it. */
  readonly emptyToolAllowlistIsInvalid: boolean;

  /** Tools this kind may register. Explicit per kind rather than "assistant
   *  minus a denylist", so a new assistant tool is not silently granted. */
  toolNames(): string[];

  /** Concurrent slots this sidecar will run for this kind. Budgets are
   *  independent: a long steward call must not consume an assistant slot. */
  slotBudget(config: AgentConfig): number;

  /** Kind-specific lease response fields. Validates them and throws on a
   *  malformed body, so the protocol client stays free of kind branches. */
  decodeLease(raw: Record<string, unknown>): Partial<LeasedJob>;

  /** Stable upstream cache key. Must not vary per run, or every run pays a cold
   *  prefix. Derived per kind because the assistant formula needs an account and
   *  a session, neither of which a space-scoped steward run has. */
  cacheKey(projection: RunContextProjection): string;

  /** Verify this kind's projection-specific fields; throw to fail closed.
   *  Called before any model request, so a mismatch costs no tokens. */
  verifyProjection(projection: RunContextProjection): void;

  /** Whether the lease response carries a server-side concurrency broadcast for
   *  this kind. Only the steward queue has a per-space budget to advertise. */
  readonly adoptsServerConcurrency: boolean;

  /** Whether the model's product travels with settlement rather than through
   *  message events. The steward's output is a closed structured product the
   *  server validates; message events are refused for child runs, so it must be
   *  reported here or it is lost. */
  readonly reportsProductOnSettle: boolean;

  /** The model's product, or null when the turn produced none.
   *  Both kinds answer with prose; what differs is where it goes — the assistant's
   *  text is published as a message event, the steward's travels with settlement.
   *  Keeping one accessor for both means "the turn produced nothing" is judged
   *  once, in the lifecycle, rather than per kind. */
  extractProduct(finalText: string | null): string | null;

  /** Path and body for one lease request. */
  leaseRequest(config: AgentConfig): LeaseRequestSpec;
}

const assistantAdapter: KindAdapter = Object.freeze<KindAdapter>({
  kind: "assistant",
  systemPrompt: () => ASSISTANT_SYSTEM_PROMPT,
  modelPrompt: (projection, userText) =>
    userText + renderContextAppendix(projection.context_blocks ?? []),
  emptyToolAllowlistIsInvalid: true,
  toolNames: () => toolNamesFor("assistant"),
  slotBudget: (config) => config.maxConcurrentRuns,
  decodeLease: () => ({}),
  // The account is part of the key because the upstream cache is scoped to the
  // provider account; two deployments sharing one upstream must not partition
  // each other's sessions. The session id keeps one conversation's prefix warm
  // across runs.
  cacheKey: (projection) => `fg-${projection.account_id}-${projection.session_id}`,
  verifyProjection: () => {
    // Nothing kind-specific to check: an assistant projection is validated by
    // the strict decoder in client.ts (it requires session_id and account_id).
  },
  adoptsServerConcurrency: false,
  reportsProductOnSettle: false,
  extractProduct: (finalText) => finalText,
  leaseRequest: (config) => ({
    path: "/internal/agent/jobs/lease",
    body: { kind: "assistant", leased_by: config.sidecarId },
  }),
});

const stewardAdapter: KindAdapter = Object.freeze<KindAdapter>({
  kind: "steward",
  systemPrompt: (projection) => {
    // Server-owned and required: see the interface comment. Failing closed here is
    // deliberate — silently falling back to a local prompt would run a model call
    // whose recorded digest describes different text.
    const instructions = projection.steward_instructions;
    if (instructions === undefined || instructions.length === 0) {
      throw new Error("steward context is missing steward_instructions");
    }
    return instructions;
  },
  modelPrompt: (projection) =>
    // The projection is the whole input: a steward run has no conversation and no
    // retrieved context to append. Wrapping it in the assistant's citation
    // appendix would add instructions the in-process carrier never sent, so the
    // two carriers would no longer be asking the same question.
    (projection.context_blocks ?? []).map((block) => block.content).join("\n"),
  // The steward's tool set is empty by design, so "must be non-empty" cannot
  // apply; the membership half still rejects any tool it does not own.
  emptyToolAllowlistIsInvalid: false,
  toolNames: () => toolNamesFor("steward"),
  slotBudget: (config) => config.stewardMaxConcurrentCallsPerSpace,
  decodeLease: (raw) => {
    // StewardLeaseOut uses steward_job_id rather than job_id: the child run has
    // no queue job, and the parent StewardJob is the authorization root.
    const stewardJobId = raw["steward_job_id"];
    if (typeof stewardJobId !== "number" || !Number.isInteger(stewardJobId)) {
      throw new InternalApiError("invalid steward lease: steward_job_id", 502, "invalid_lease");
    }
    const attemptId = raw["assist_attempt_id"];
    const maxConcurrent = raw["max_concurrent"];
    if (typeof maxConcurrent !== "number" || !Number.isInteger(maxConcurrent) || maxConcurrent < 1) {
      throw new InternalApiError("invalid steward lease: max_concurrent", 502, "invalid_lease");
    }
    return {
      steward_job_id: String(stewardJobId),
      steward_attempt_id:
        typeof attemptId === "number" && Number.isInteger(attemptId) ? String(attemptId) : null,
      assist_kind: typeof raw["assist_kind"] === "string" ? raw["assist_kind"] : undefined,
      max_concurrent: maxConcurrent,
    };
  },
  // A steward run has no account and no session, so the assistant formula would
  // yield `fg-null-null` for every run and let unrelated spaces share one
  // upstream cache prefix. The space is the natural steward scope and the only
  // stable identifier the projection carries; the prefix it recovers is the
  // system prompt, which is space-independent.
  cacheKey: (projection) => `fg-steward-${projection.space_id}`,
  verifyProjection: (projection) => {
    // The steward prompt lives in this image, so the server can no longer hash
    // it. Verify the version instead of trusting that the deployed image matches
    // the backend: running stale prompt text against a newer server would
    // silently change what the model is asked to do, and the evaluation anchor
    // would point at a prompt nobody is using.
    const expected = projection.steward_prompt_version;
    if (expected === undefined) {
      throw new Error("steward context is missing steward_prompt_version");
    }
    if (expected !== STEWARD_PROMPT_VERSION) {
      throw new Error(
        `steward prompt version mismatch: server expects ${expected}, ` +
          `this sidecar has ${STEWARD_PROMPT_VERSION}`,
      );
    }
    if (projection.steward_instructions === undefined) {
      throw new Error("steward context is missing steward_instructions");
    }
  },
  adoptsServerConcurrency: true,
  reportsProductOnSettle: true,
  extractProduct: (finalText) => finalText,
  // The endpoint is named for what is leased (an attempt), not for the job that
  // used to be the unit, and it is a separate route from the assistant queue:
  // "which container may lease which queue" is a routing-level constraint, not
  // payload validation. The body carries no space_id — the sidecar has no view
  // of the space topology and the server decides whose work to hand out.
  leaseRequest: (config) => ({
    path: "/internal/agent/steward/attempts/lease",
    body: { kind: "steward", leased_by: config.sidecarId },
  }),
});

const ADAPTERS: Record<AgentKind, KindAdapter> = {
  assistant: assistantAdapter,
  steward: stewardAdapter,
};

/** The adapter for one kind. Total: every AgentKind has exactly one. */
export function adapterFor(kind: AgentKind): KindAdapter {
  return ADAPTERS[kind];
}
