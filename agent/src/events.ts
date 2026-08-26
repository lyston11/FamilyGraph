/**
 * Event schema: explicit conversion from Pi AgentSessionEvent broadcasts to
 * FamilyGraph run events. Provider-private payloads are never forwarded;
 * every event type has a closed, whitelisted payload shape (notes.md registry).
 *
 * Registry (V2.1): run.started, message.user_added, turn.started,
 * turn.completed, message.assistant_added, tool.execution.started,
 * tool.execution.completed, run.settled, run.failed, run.cancelled.
 * card.* is a reserved namespace for V2.4 and must not be emitted here.
 */

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
};
export type FgEventPayload = FgEventPayloadMap[FgEventType];

export interface FgEvent {
  /** Sender-assigned monotonic sequence within the run (1-based). */
  seq: number;
  type: FgEventType;
  /** Wire field name per backend EventIn schema (strict extra=forbid). */
  public_payload: FgEventPayload;
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

/**
 * Deterministic Pi→FG event mapper. Returns the list of FG events generated
 * for one session broadcast event; unknown/ignored session events yield [].
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
      return [
        {
          type: "message.assistant_added",
          // Whitelisted projection only: role + concatenated text blocks.
          // Thinking blocks, usage, provider ids, raw tool calls are dropped.
          public_payload: { role: "assistant", text: extractText(event.message.content) },
        },
      ];
    }
    case "tool_execution_start":
      return [
        {
          type: "tool.execution.started",
          public_payload: {
            tool_call_id: String(event.toolCallId ?? ""),
            tool_name: String(event.toolName ?? ""),
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
            tool_name: String(event.toolName ?? ""),
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
 */
export class RunEventBuffer {
  private nextSeq = 1;
  private readonly pending: FgEvent[] = [];

  push<T extends FgEventType>(type: T, public_payload: FgEventPayloadMap[T]): void {
    this.pending.push({ seq: this.nextSeq++, type, public_payload });
  }

  /** Feed one session broadcast; returns count of produced events. */
  onSessionEvent(event: SessionEventLike): number {
    const mapped = mapSessionEvent(event);
    for (const item of mapped) {
      this.push(item.type, item.public_payload);
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
