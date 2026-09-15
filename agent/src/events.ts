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

export interface FgEvent {
  /** Sender-assigned monotonic sequence within the run (1-based). */
  seq: number;
  type: FgEventType;
  /** Wire field name per backend EventIn schema (strict extra=forbid). */
  public_payload: FgEventPayload;
  /** Private submission binding; never part of the public event payload. */
  context_reference?: ContextReference;
}

type SessionEventLike = {
  type: string;
  message?: {
    role?: string;
    content?: unknown;
    [key: string]: unknown;
  };
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
  args?: unknown;
  result?: unknown;
  [key: string]: unknown;
};

function extractText(content: unknown): string {
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
      // A turn that carries only tool calls has no prose to show the user: the
      // tool calls themselves are reported by tool.execution.started/completed.
      // The judgement states the intent ("this message only carries tool
      // calls") instead of enumerating provider stopReason values.
      const hasToolCall =
        Array.isArray(event.message.content) &&
        event.message.content.some(
          (block) =>
            typeof block === "object" &&
            block !== null &&
            (block as { type?: unknown }).type === "toolCall",
        );
      if (text.length === 0 && hasToolCall) return [];
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

  constructor(startSeq = 1, private readonly context?: {
    build_id: number; attempt: number; allowed_handles: readonly string[];
  }) {
    this.nextSeq = Number.isInteger(startSeq) && startSeq >= 0 ? startSeq : 1;
  }

  push<T extends FgEventType>(type: T, public_payload: FgEventPayloadMap[T], context_reference?: ContextReference): void {
    this.pending.push({ seq: this.nextSeq++, type, public_payload,
      ...(context_reference === undefined ? {} : { context_reference }) });
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
      this.push(item.type, item.public_payload, reference);
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
