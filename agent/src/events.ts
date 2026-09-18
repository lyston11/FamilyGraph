/**
 * Event schema: explicit conversion from Pi AgentSessionEvent broadcasts to
 * FamilyGraph run events. Provider-private payloads are never forwarded;
 * every event type has a closed, whitelisted payload shape (notes.md registry).
 *
 * Registry (V2.1): run.started, message.user_added, turn.started,
 * turn.completed, message.assistant_added, tool.execution.started,
 * tool.execution.completed, run.settled, run.failed, run.cancelled, run.expired.
 * card.* is a reserved namespace for V2.4 and must not be emitted here.
 *
 * message.user_added and all terminal events (run.settled/run.failed/
 * run.cancelled/run.expired) are backend-owned; this sidecar emits only
 * run.started, turn.*, message.assistant_added and tool.execution.*.
 */

import { canonicalToolName } from "./tools.js";

export const EVENT_TYPES = [
  "run.started",
  "message.user_added",
  "turn.started",
  "turn.completed",
  "message.assistant_added",
  "tool.execution.started",
  "tool.execution.completed",
  "run.settled",
  "run.failed",
  "run.cancelled",
  "run.expired",
] as const;

export type FgEventType = (typeof EVENT_TYPES)[number];

export function isKnownEventType(value: string): value is FgEventType {
  return (EVENT_TYPES as readonly string[]).includes(value);
}

/** Closed payload shapes — additional keys are a programming error. */
export interface RunStartedPayload {
  error_code?: undefined;
}
export interface UserMessagePayload {
  role: "user";
  text: string;
}
export interface AssistantMessagePayload {
  role: "assistant";
  text: string;
  /** Web citations collected from fetch_approved_page during this turn. */
  web_citations?: WebCitationPayload[];
}

/** Bounded external citation projection (backend WebCitationOut). */
export interface WebCitationPayload {
  url: string;
  title: string;
  excerpt: string;
  fetched_at: string;
  trust: "external";
}
export interface ToolExecutionStartedPayload {
  tool_call_id: string;
  tool_name: string;
  tool_version: number;
}
export interface ToolExecutionCompletedPayload {
  tool_call_id: string;
  tool_name: string;
  /** Error flag only — raw tool results are never broadcast. */
  is_error: boolean;
}
export interface TerminalPayload {
  error_code?: string;
  message?: string;
}
export type FgEventPayloadMap = {
  "run.started": Record<string, never>;
  "message.user_added": UserMessagePayload;
  "turn.started": Record<string, never>;
  "turn.completed": Record<string, never>;
  "message.assistant_added": AssistantMessagePayload;
  "tool.execution.started": ToolExecutionStartedPayload;
  "tool.execution.completed": ToolExecutionCompletedPayload;
  "run.settled": TerminalPayload;
  "run.failed": TerminalPayload;
  "run.cancelled": TerminalPayload;
  "run.expired": TerminalPayload;
};
export type FgEventPayload = FgEventPayloadMap[FgEventType];

export interface ContextReference {
  build_id: number;
  attempt: number;
  used_handles: string[];
}

/**
 * Producer-measured stage duration (backend `schemas/agent.EventTimingIn`).
 *
 * ``duration_ms`` is the monotonic duration of the stage that ENDS at this
 * event: for ``run.started`` it is "sidecar received lease → SDK agent_start"
 * (context fetch + session creation); for ``message.assistant_added`` it is
 * that turn's ``turn_start`` → ``message_end``; for
 * ``tool.execution.completed`` it is that call's start → end.
 *
 * The backend persists it in ``agent_run_events.timing_json`` — never in
 * ``public_payload`` — because ``created_at`` is the persistence time and the
 * 250ms flush batching makes it useless for short stages.
 */
export interface EventTiming {
  source: "sidecar-v1";
  duration_ms: number;
  /**
   * SDK compaction (`compaction_start` → `compaction_end`) accumulated inside
   * this turn. It is a SUB-COMPONENT of ``duration_ms`` (the summarization
   * request happens inside the turn), so `model_turn` must never be read as pure
   * generation without it. Omitted when the turn had no compaction.
   */
  compaction_ms?: number;
  /**
   * ``turn_start`` → first ``text_delta``. Sub-component of ``duration_ms``.
   *
   * This is the quantity that separates "the model took 33s to answer" from
   * "the model answered early but the text stayed invisible until the whole
   * message finished": the public assistant event is still published only at
   * ``message_end``. Thinking deltas do NOT count — the user cannot read them.
   * Omitted when the turn produced no prose at all (tool-only turn).
   */
  first_text_ms?: number;
  /**
   * Pi session auto-retries observed inside this turn and the backoff they
   * scheduled. Recorded because a failed attempt is otherwise
   * indistinguishable from one slow generation in the persisted event stream
   * (15s request timeout + 2s backoff + 16s looks exactly like a 33s turn).
   * Omitted when the turn had no auto-retry.
   */
  retry_count?: number;
  retry_wait_ms?: number;
}

export interface FgEvent {
  /** Sender-assigned monotonic sequence within the run (1-based). */
  seq: number;
  type: FgEventType;
  /** Wire field name per backend EventIn schema (strict extra=forbid). */
  public_payload: FgEventPayload;
  /** Private submission binding; never part of the public event payload. */
  context_reference?: ContextReference;
  /** Private producer timing; never part of the public event payload. */
  timing?: EventTiming;
}

type SessionEventLike = {
  type: string;
  message?: {
    role?: string;
    content?: unknown;
    [key: string]: unknown;
  };
  /** Present on `message_update`; carries the streaming delta kind. */
  assistantMessageEvent?: { type?: string; delta?: string; [key: string]: unknown };
  /** Present on `auto_retry_start`: the backoff the SDK scheduled. */
  delayMs?: number;
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
  args?: unknown;
  result?: unknown;
  [key: string]: unknown;
};

/**
 * Concatenate the text blocks of a Pi message content array. Exported because
 * the worker reuses this exact predicate to decide whether a run produced an
 * answer; two copies would drift.
 */
export function extractText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (block): block is { type: "text"; text: string } =>
        typeof block === "object" &&
        block !== null &&
        (block as { type?: unknown }).type === "text" &&
        typeof (block as { text?: unknown }).text === "string",
    )
    .map((block) => block.text)
    .join("");
}

function canonicalEventToolName(value: unknown): string {
  const raw = String(value ?? "");
  return canonicalToolName(raw) ?? raw;
}

/** Extract a bounded web citation from a fetch_approved_page tool result. */
function extractWebCitation(result: unknown): WebCitationPayload | null {
  if (result === null || typeof result !== "object") return null;
  const raw = result as Record<string, unknown>;
  const citation = raw["citation"];
  if (citation === null || typeof citation !== "object") return null;
  const c = citation as Record<string, unknown>;
  if (
    typeof c["url"] !== "string" ||
    typeof c["title"] !== "string" ||
    typeof c["excerpt"] !== "string" ||
    typeof c["fetched_at"] !== "string" ||
    c["trust"] !== "external"
  ) {
    return null;
  }
  return {
    url: c["url"],
    title: c["title"],
    excerpt: c["excerpt"].slice(0, 4000),
    fetched_at: c["fetched_at"],
    trust: "external",
  };
}

/**
 * Deterministic Pi→FG event mapper. Returns the list of FG events generated
 * for one session broadcast event; unknown/ignored session events yield [].
 *
 * Web citations are collected from fetch_approved_page tool results and
 * attached to the next assistant message; the buffer owns that state.
 */
export function mapSessionEvent(event: SessionEventLike): Array<Omit<FgEvent, "seq">> {
  switch (event.type) {
    case "agent_start":
      return [{ type: "run.started", public_payload: {} }];
    case "turn_start":
      return [{ type: "turn.started", public_payload: {} }];
    case "turn_end":
      return [{ type: "turn.completed", public_payload: {} }];
    case "message_end": {
      if (event.message?.role !== "assistant") return [];
      const text = extractText(event.message.content);
      // An empty assistant message has nothing to show the user, whatever the
      // stop reason: a tool-only turn is already reported by
      // tool.execution.started/completed, and an empty final answer has no
      // content at all. Emitting it would persist a blank assistant row (later
      // replayed as empty history) and render a blank bubble.
      if (text.length === 0) return [];
      return [
        {
          type: "message.assistant_added",
          // Whitelisted projection only: role + concatenated text blocks.
          // Thinking blocks, usage, provider ids, raw tool calls are dropped.
          public_payload: { role: "assistant", text },
        },
      ];
    }
    case "tool_execution_start":
      return [
        {
          type: "tool.execution.started",
          public_payload: {
            tool_call_id: String(event.toolCallId ?? ""),
            tool_name: canonicalEventToolName(event.toolName),
            tool_version: 1,
          },
        },
      ];
    case "tool_execution_end":
      return [
        {
          type: "tool.execution.completed",
          public_payload: {
            tool_call_id: String(event.toolCallId ?? ""),
            tool_name: canonicalEventToolName(event.toolName),
            // Raw tool results are never broadcast; only the error flag.
            is_error: Boolean(event.isError),
          },
        },
      ];
    default:
      // message_update/streaming deltas, agent_end, agent_settled, queue
      // updates etc. are intentionally not persisted.
      return [];
  }
}

/**
 * Ordered buffer of events for one run. The worker appends user/system-level
 * events directly and feeds session broadcasts through `onSessionEvent`.
 *
 * Web citations from fetch_approved_page tool results are collected here and
 * attached to the next assistant message (then cleared), so a turn's external
 * sources travel with the answer that used them.
 */
export class RunEventBuffer {
  private nextSeq: number;
  private readonly pending: FgEvent[] = [];
  private webCitations: WebCitationPayload[] = [];
  /** Monotonic start of the current turn; cleared on each turn_start. */
  private turnStartedAt: number | null = null;
  /** Open tool executions by tool_call_id (start timestamp). */
  private readonly openTools = new Map<string, number>();
  /** Monotonic start of an in-flight SDK compaction (start → end). */
  private compactionStartedAt: number | null = null;
  /** Compaction ms accumulated inside the current turn (sub-component). */
  private turnCompactionMs = 0;
  /**
   * Monotonic time of this turn's first assistant text delta, or null when the
   * turn has produced no prose yet. Thinking deltas never set it.
   */
  private firstTextAt: number | null = null;
  /** Auto-retries observed inside the current turn, with their scheduled backoff. */
  private turnRetryCount = 0;
  private turnRetryWaitMs = 0;

  constructor(
    startSeq = 1,
    private readonly context?: {
      build_id: number;
      attempt: number;
      allowed_handles: readonly string[];
    },
    private readonly timing?: {
      /** Monotonic ms when the sidecar received the lease (stage origin). */
      prepStartedAt: number;
      /** Injectable clock for tests; defaults to the process monotonic clock. */
      now?: () => number;
    },
  ) {
    this.nextSeq = Number.isInteger(startSeq) && startSeq >= 0 ? startSeq : 1;
  }

  private now(): number {
    return this.timing?.now ? this.timing.now() : performance.now();
  }

  /**
   * Timing for a stage ending now, or undefined when the stage has no origin
   * (no injected prep origin, unpaired tool start, no observed turn_start).
   * Negative deltas are dropped rather than clamped: a clock anomaly must not
   * be reported as a real 0ms stage.
   */
  private elapsedFrom(origin: number | null): EventTiming | undefined {
    if (origin === null) return undefined;
    const duration = Math.round(this.now() - origin);
    if (!Number.isFinite(duration) || duration < 0) return undefined;
    return { source: "sidecar-v1", duration_ms: duration };
  }

  /**
   * Milliseconds between two recorded origins, with the same guard as
   * `elapsedFrom`: a missing origin or a backwards clock yields undefined
   * rather than a fabricated 0ms stage.
   */
  private spanBetween(start: number | null, end: number | null): number | undefined {
    if (start === null || end === null) return undefined;
    const duration = Math.round(end - start);
    if (!Number.isFinite(duration) || duration < 0) return undefined;
    return duration;
  }

  push<T extends FgEventType>(
    type: T,
    public_payload: FgEventPayloadMap[T],
    context_reference?: ContextReference,
    timing?: EventTiming,
  ): void {
    this.pending.push({
      seq: this.nextSeq++,
      type,
      public_payload,
      ...(context_reference === undefined ? {} : { context_reference }),
      ...(timing === undefined ? {} : { timing }),
    });
  }

  /** Feed one session broadcast; returns count of produced events. */
  onSessionEvent(event: SessionEventLike): number {
    if (
      event.type === "tool_execution_end" &&
      canonicalEventToolName(event.toolName) === "familygraph.fetch_approved_page"
    ) {
      const citation = extractWebCitation(event.result);
      if (citation !== null) this.webCitations.push(citation);
    }
    // Stage origins are recorded before mapping so the event that ends a stage
    // can measure it; turn_start/tool_execution_start produce no FG event.
    if (event.type === "turn_start") {
      this.turnStartedAt = this.now();
      // Each turn owns its own compaction budget; a later turn must not inherit
      // an earlier turn's summarization cost.
      this.turnCompactionMs = 0;
      // Same for first-text and retry accounting: a clean turn after a retried
      // one must not inherit either.
      this.firstTextAt = null;
      this.turnRetryCount = 0;
      this.turnRetryWaitMs = 0;
    }
    // First *readable* prose of this turn. `thinking_delta` is excluded on
    // purpose: the user cannot see reasoning, so counting it would understate
    // the wait the PRD's 3s first-segment target is about.
    if (
      event.type === "message_update" &&
      event.assistantMessageEvent?.type === "text_delta" &&
      typeof event.assistantMessageEvent.delta === "string" &&
      event.assistantMessageEvent.delta.length > 0 &&
      this.firstTextAt === null
    ) {
      this.firstTextAt = this.now();
    }
    if (event.type === "auto_retry_start") {
      this.turnRetryCount += 1;
      const delay = Number(event.delayMs);
      if (Number.isFinite(delay) && delay > 0) this.turnRetryWaitMs += delay;
    }
    if (event.type === "compaction_start") {
      this.compactionStartedAt = this.now();
    }
    if (event.type === "compaction_end" && this.compactionStartedAt !== null) {
      const elapsed = this.now() - this.compactionStartedAt;
      if (Number.isFinite(elapsed) && elapsed > 0) this.turnCompactionMs += elapsed;
      this.compactionStartedAt = null;
    }
    let toolStartedAt: number | null = null;
    if (event.type === "tool_execution_start") {
      const key = String(event.toolCallId ?? "");
      const started = this.now();
      this.openTools.set(key, started);
      toolStartedAt = started;
    }

    const mapped = mapSessionEvent(event);
    for (const item of mapped) {
      if (item.type === "message.assistant_added" && this.webCitations.length > 0) {
        const payload = item.public_payload as AssistantMessagePayload;
        payload.web_citations = this.webCitations.slice();
        this.webCitations = [];
      }
      let reference: ContextReference | undefined;
      if (item.type === "message.assistant_added" && event.type === "message_end" &&
          event.message?.stopReason === "stop" && this.context !== undefined) {
        const text = (item.public_payload as AssistantMessagePayload).text;
        const mentioned = new Set(Array.from(text.matchAll(/\[(rag:[^\s\]]{1,250})\]/gu), (m) => m[1]));
        const handles = [...new Set(this.context.allowed_handles)]
          .filter((handle) => mentioned.has(handle) && handle.length <= 255).slice(0, 20);
        reference = { build_id: this.context.build_id, attempt: this.context.attempt, used_handles: handles };
      }
      let timing: EventTiming | undefined;
      if (item.type === "run.started") {
        timing = this.elapsedFrom(this.timing?.prepStartedAt ?? null);
      } else if (item.type === "message.assistant_added") {
        timing = this.elapsedFrom(this.turnStartedAt);
        if (timing !== undefined) {
          // Attach this turn's bounded sub-components so the backend can
          // separate summarization, pre-prose reasoning and retries from pure
          // generation instead of reading `model_turn` as one number.
          const firstTextMs = this.spanBetween(this.turnStartedAt, this.firstTextAt);
          timing = {
            ...timing,
            ...(this.turnCompactionMs > 0
              ? { compaction_ms: Math.round(this.turnCompactionMs) }
              : {}),
            ...(firstTextMs === undefined ? {} : { first_text_ms: firstTextMs }),
            ...(this.turnRetryCount > 0
              ? {
                  retry_count: this.turnRetryCount,
                  retry_wait_ms: Math.round(this.turnRetryWaitMs),
                }
              : {}),
          };
        }
        this.turnCompactionMs = 0;
        this.firstTextAt = null;
        this.turnRetryCount = 0;
        this.turnRetryWaitMs = 0;
      } else if (item.type === "tool.execution.completed") {
        const key = String((item.public_payload as ToolExecutionCompletedPayload).tool_call_id);
        timing = this.elapsedFrom(this.openTools.get(key) ?? toolStartedAt);
        this.openTools.delete(key);
      }
      this.push(item.type, item.public_payload, reference, timing);
    }
    return mapped.length;
  }

  drain(): FgEvent[] {
    const out = this.pending.splice(0, this.pending.length);
    return out.sort((a, b) => a.seq - b.seq);
  }

  get size(): number {
    return this.pending.length;
  }
}
