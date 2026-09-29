/**
 * familygraph-policy-guard — the synchronous model-boundary policy barrier.
 *
 * This extension is deliberately lightweight and has no network/database
 * access. FastAPI remains authoritative for data and tool authorization.
 *
 * Division of responsibility (2026-09-29 task 09-29-agent-policy-boundary):
 *
 * - FastAPI owns authorization: space/user/run/attempt fences, the tool
 *   allowlist, closed argument schemas, membership, visibility projection,
 *   provider local/cloud policy and Steward product write-back validation.
 * - This guard owns the *synchronous* boundary: it must not widen what FastAPI
 *   granted, and it must not fail a run on natural-language wording alone.
 *
 * Keyword markers are a diagnostic signal, never an authorization decision.
 * A model that writes "system prompt" is not an attack; a tool call outside the
 * allowlist is. Only the latter blocks.
 */

import type { InlineExtension } from "@earendil-works/pi-coding-agent";
import { canonicalToolName } from "./tools.js";

const INJECTION_MARKERS = [
  "ignore previous instructions",
  "ignore all previous instructions",
  "disregard previous instructions",
  "forget previous instructions",
  "忽略之前的指令",
  "忽略系统提示",
  "system message",
  "system prompt",
  "developer message",
  "call the hidden tool",
  "reveal hidden information",
  "show hidden information",
  "调用隐藏工具",
  "显示隐藏信息",
  "绕过限制",
  "作为管理员",
];

const MASKED_TEXT_PATTERN = /\bmasked\b|遮罩|已脱敏/i;

const PII_PATTERNS = [
  /\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/,
  /\b\d{3}[- ]?\d{2}[- ]?\d{4}\b/,
  /\b\d{16,19}\b/,
  /(?<!\d)(?=(?:\D*\d){10,})(?:\+?\d[\d -]{8,}\d)(?!\d)/,
];

const DEFAULT_MAX_TOOL_RESULT_CHARS = 32_000;
const DEFAULT_MAX_TOOL_INPUT_CHARS = 32_000;
const SCOPE_KEYS = new Set([
  "actor",
  "actor_id",
  "account",
  "account_id",
  "agent",
  "agent_id",
  "agent_kind",
  "run_id",
  "scope",
  "space",
  "space_id",
  "include_private",
  "raw",
  "unmasked",
  "visibility",
  "tool_allowlist",
]);

type ProviderKind = "local" | "openai_compatible";

/**
 * Hard-block classes. Every blocking decision maps to exactly one of these, so
 * the settled error code states what was actually detected. In particular no
 * non-secret failure may be reported as a secret leak.
 */
export type PolicyBlockCode =
  | "POLICY_SECRET_IN_PROVIDER_PAYLOAD"
  | "POLICY_TOOL_BLOCKED"
  | "POLICY_TOOL_RESULT_BLOCKED"
  | "POLICY_PROVIDER_BLOCKED"
  | "POLICY_MASKED_DATA"
  | "POLICY_GUARD_BLOCKED";

/** Blocking violation kinds. Each one is evidence of a real policy violation. */
type ViolationKind =
  | "tool_not_allowed"
  | "unsafe_tool_arguments"
  | "tool_result_too_large"
  | "masked_data"
  | "local_provider_required"
  | "cloud_provider_forbidden"
  | "secret_in_provider_payload";

/** Non-blocking observations. They never fail a run on their own. */
type NoticeKind =
  | "sensitive_redacted"
  | "pii_redacted"
  | "unconfirmed_fact_annotated"
  | "instruction_like_text"
  | "masked_text";

const BLOCK_CODE_BY_VIOLATION: Record<ViolationKind, PolicyBlockCode> = {
  secret_in_provider_payload: "POLICY_SECRET_IN_PROVIDER_PAYLOAD",
  tool_not_allowed: "POLICY_TOOL_BLOCKED",
  unsafe_tool_arguments: "POLICY_TOOL_BLOCKED",
  tool_result_too_large: "POLICY_TOOL_RESULT_BLOCKED",
  local_provider_required: "POLICY_PROVIDER_BLOCKED",
  cloud_provider_forbidden: "POLICY_PROVIDER_BLOCKED",
  masked_data: "POLICY_MASKED_DATA",
};

/** Fixed rule identifiers. Never build one from content or an unknown field. */
const RULES = {
  injectionMarker: "instruction_marker",
  maskedText: "masked_wording",
  maskedContract: "masked_contract_field",
  contextContract: "context_contract",
  secretPayload: "secret_in_provider_payload",
  secretToolArgs: "secret_in_tool_arguments",
  scopeOverride: "scope_override",
  toolNotAllowed: "tool_not_allowed",
  toolArgsTooLarge: "tool_arguments_too_large",
  toolResultTooLarge: "tool_result_too_large",
  toolResultContract: "tool_result_contract",
  providerLocalRequired: "local_provider_required",
  providerCloudForbidden: "cloud_provider_forbidden",
  unconfirmed: "unconfirmed_fact",
  piiRedacted: "pii_redacted",
  sensitiveRedacted: "sensitive_redacted",
} as const;

type PolicyStage =
  | "input"
  | "tool_call"
  | "tool_result"
  | "context"
  | "before_provider_request"
  | "tool_execution_end";

type PolicySource = "user" | "assistant" | "tool" | "payload" | "unknown";

type PolicyAction = "block" | "notice" | "sanitize" | "annotate";

export interface PolicyViolation {
  kind: ViolationKind;
  detail: string;
}

export interface PolicyNotice {
  kind: NoticeKind;
  detail: string;
}

/**
 * One log-safe diagnostic record. It deliberately carries no source text, no
 * match fragment, no prompt, no thinking, no tool input/output and no content
 * hash: correlation uses run-scoped coordinates only.
 */
export interface PolicyIncident {
  rule: string;
  stage: PolicyStage;
  source: PolicySource;
  action: PolicyAction;
  occurrences: number;
  turnIndex?: number;
  messageIndex?: number;
  toolCallId?: string;
}

export interface PolicyGuardOptions {
  /** Server-issued domain-tool allowlist for this run. */
  allowlist: ReadonlySet<string>;
  /** Secret strings that must never appear in provider payloads or results. */
  secrets: readonly string[];
  /** Provider selected by FastAPI for this run. */
  providerKind?: ProviderKind;
  /** True when prefetched context requires a local provider. */
  localRequired?: boolean;
  /** Whether a non-local provider may be used for this run. */
  cloudAllowed?: boolean;
  /** Maximum serialized tool result sent back to the model. */
  maxToolResultChars?: number;
  /** Maximum serialized tool-call arguments accepted by the guard. */
  maxToolInputChars?: number;
  onViolation?: (violation: PolicyViolation) => void;
  onNotice?: (notice: PolicyNotice) => void;
  /** Fired once, when this run first becomes hard-blocked. */
  onBlock?: (incident: PolicyIncident, code: PolicyBlockCode) => void;
  onIncident?: (incident: PolicyIncident) => void;
  onSettled?: (event: { type: "agent_settled" }) => void;
}

export interface PolicyGuard {
  readonly extension: InlineExtension;
  readonly violationCount: number;
  readonly blockingViolationCount: number;
  readonly violations: readonly PolicyViolation[];
  readonly notices: readonly PolicyNotice[];
  readonly incidents: readonly PolicyIncident[];
  /** Total diagnostics seen, including those folded into `occurrences`. */
  readonly incidentTotal: number;
  /** Diagnostics beyond the bounded retention window (counted, not stored). */
  readonly incidentsDropped: number;
  /** True once this run has taken an irreversible hard-block decision. */
  readonly blocked: boolean;
  /** The first hard-block class, or null while the run is still allowed. */
  readonly blockCode: PolicyBlockCode | null;
  /** Applies the final provider-boundary check without network/database I/O. */
  readonly beforeProviderRequest: (payload: unknown) => unknown;
}

/** Cache of non-credential request fields that merely contain "token". */
const NON_CREDENTIAL_TOKEN_FIELDS = new Set([
  "max_tokens",
  "max_completion_tokens",
  "max_output_tokens",
  "include_usage",
  "stream_options",
]);

const CREDENTIAL_KEY_RE =
  /^(?:access|auth|bearer|refresh|id|session|api|provider|client|personal|customer)?[-_]?(?:tokens?|api[-_]?key|secret|password)$|^(?:authorization|secret|password|api[-_]?key)$/i;

function isCredentialKey(key: string): boolean {
  if (NON_CREDENTIAL_TOKEN_FIELDS.has(key)) return false;
  return CREDENTIAL_KEY_RE.test(key);
}

/** Recursively redacts secret occurrences and sensitive object keys. */
export function redactSecrets(value: unknown, secrets: readonly string[]): unknown {
  if (typeof value === "string") {
    let out = value;
    for (const secret of secrets) {
      if (secret.length > 0) out = out.split(secret).join("[REDACTED]");
    }
    return out;
  }
  if (Array.isArray(value)) return value.map((item) => redactSecrets(item, secrets));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = isCredentialKey(key) ? "[REDACTED]" : redactSecrets(item, secrets);
    }
    return out;
  }
  return value;
}

function serialized(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return "";
  }
}

function normalizedText(value: unknown): string {
  return serialized(value).toLowerCase().replace(/\s+/g, " ");
}

/** Diagnostic only: natural-language markers never authorize or block. */
function containsInjection(value: unknown): boolean {
  const text = normalizedText(value);
  return INJECTION_MARKERS.some((marker) => text.includes(marker));
}

/** Diagnostic only: prose that talks about masking is not masked data. */
function containsMaskedWording(value: unknown): boolean {
  if (typeof value === "string") return MASKED_TEXT_PATTERN.test(value);
  if (Array.isArray(value)) return value.some(containsMaskedWording);
  if (value === null || typeof value !== "object") return false;
  return Object.values(value as Record<string, unknown>).some(containsMaskedWording);
}

/**
 * Contract check: only a server-shaped field decides that restricted data is
 * present. A model or user writing `masked` in prose cannot create this signal,
 * and this signal cannot be suppressed by rewording.
 *
 * Tool results reach the sidecar as JSON text, so a serialized payload is
 * parsed to find the *field*; the word itself is never the evidence.
 */
function containsMaskedContract(value: unknown): boolean {
  if (typeof value === "string") {
    const text = value.trim();
    if (!text.startsWith("{") && !text.startsWith("[")) return false;
    try {
      return containsMaskedContract(JSON.parse(text));
    } catch {
      return false;
    }
  }
  if (Array.isArray(value)) return value.some(containsMaskedContract);
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>).some(([key, item]) => {
    const normalizedKey = key.toLowerCase().replaceAll("-", "_");
    return (
      (normalizedKey === "visibility" && String(item).toLowerCase() === "masked") ||
      (normalizedKey === "masked" && item === true) ||
      containsMaskedContract(item)
    );
  });
}

function containsSecret(value: unknown, secrets: readonly string[]): boolean {
  const text = serialized(value);
  return secrets.some((secret) => secret.length > 0 && text.includes(secret));
}

function containsPii(value: unknown): boolean {
  const text = serialized(value);
  return PII_PATTERNS.some((pattern) => pattern.test(text));
}

function redactPii(value: unknown): unknown {
  if (typeof value === "string") {
    return PII_PATTERNS.reduce((text, pattern) => text.replace(pattern, "[REDACTED]"), value);
  }
  if (Array.isArray(value)) return value.map(redactPii);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        /^(?:address|birth[_-]?date|card[_-]?number|credit[_-]?card|date[_-]?of[_-]?birth|email|national[_-]?id|phone|postal[_-]?code|ssn|street|telephone|zip[_-]?code)$/i.test(
          key,
        )
          ? "[REDACTED]"
          : redactPii(item),
      ]),
    );
  }
  return value;
}

function redactSensitive(value: unknown, secrets: readonly string[]): unknown {
  return redactPii(redactSecrets(value, secrets));
}

function containsScopeOverride(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsScopeOverride);
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>).some(
    ([key, item]) =>
      SCOPE_KEYS.has(key.toLowerCase().replaceAll("-", "_")) || containsScopeOverride(item),
  );
}

function isUnconfirmed(value: unknown): boolean {
  if (typeof value === "string") {
    if (/\b(?:unconfirmed|pending|proposed|disputed)\b|未经确认|待确认|有争议/i.test(value))
      return true;
    try {
      return isUnconfirmed(JSON.parse(value));
    } catch {
      return false;
    }
  }
  if (Array.isArray(value)) return value.some(isUnconfirmed);
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>).some(([key, item]) => {
    const normalizedKey = key.toLowerCase().replaceAll("-", "_");
    return (
      (normalizedKey === "confirmed" && item === false) ||
      (normalizedKey === "confirmation_status" &&
        ["pending", "proposed", "unconfirmed", "disputed"].includes(String(item))) ||
      (normalizedKey === "fact_state" &&
        ["pending", "proposed", "unconfirmed", "disputed"].includes(String(item))) ||
      isUnconfirmed(item)
    );
  });
}

function annotateUnconfirmed(value: unknown): unknown {
  const label = unconfirmedLabel();
  if (Array.isArray(value)) return [label, ...value];
  return [label, { type: "text", text: serialized(value) }];
}

function safeLimit(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

function truncatedText(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  const marker = "...[TRUNCATED]";
  if (maxChars <= marker.length) return marker.slice(0, maxChars);
  return `${value.slice(0, maxChars - marker.length)}${marker}`;
}

function unconfirmedLabel(): { type: "text"; text: string } {
  return { type: "text", text: "[UNCONFIRMED FACT: verify this data before relying on it]" };
}

function boundedResultText(raw: string, maxChars: number, unconfirmed: boolean): string {
  const prefix = unconfirmed ? "[UNCONFIRMED] " : "";
  if (prefix.length >= maxChars) return prefix.slice(0, maxChars);
  return `${prefix}${truncatedText(raw, maxChars - prefix.length)}`;
}

/**
 * Neutral wording for a withheld result. It must not itself contain a marker
 * phrase, or the guard would flag its own safety notice on the next pass.
 */
const WITHHELD_RESULT_TEXT = "[FamilyGraph restricted content withheld by policy]";

function sanitizeToolResult(
  content: unknown,
  secrets: readonly string[],
  maxChars: number,
): {
  content: unknown;
  redacted: boolean;
  oversized: boolean;
  unconfirmed: boolean;
  maskedContract: boolean;
  maskedWording: boolean;
} {
  if (containsMaskedContract(content)) {
    return {
      content: [{ type: "text", text: WITHHELD_RESULT_TEXT }],
      redacted: false,
      oversized: false,
      unconfirmed: false,
      maskedContract: true,
      maskedWording: false,
    };
  }
  const sanitized = redactSensitive(content, secrets);
  const redacted = serialized(sanitized) !== serialized(content);
  const unconfirmed = isUnconfirmed(content);
  const maskedWording = containsMaskedWording(content);
  const annotated = unconfirmed ? annotateUnconfirmed(sanitized) : sanitized;
  const raw = serialized(annotated);
  if (raw.length <= maxChars) {
    return {
      content: annotated,
      redacted,
      oversized: false,
      unconfirmed,
      maskedContract: false,
      maskedWording,
    };
  }
  const bounded = boundedResultText(raw, maxChars, unconfirmed);
  return {
    content: [{ type: "text", text: bounded }],
    redacted,
    oversized: true,
    unconfirmed,
    maskedContract: false,
    maskedWording,
  };
}

function canonicalPolicyToolName(value: string): string {
  return canonicalToolName(value) ?? value;
}

/** Opaque call ids are locators, not content: bound them to a safe charset. */
function safeToolCallId(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const cleaned = value.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64);
  return cleaned.length > 0 ? cleaned : undefined;
}

function sourceFromRole(role: unknown): PolicySource {
  if (role === "user") return "user";
  if (role === "assistant") return "assistant";
  if (role === "toolResult" || role === "tool") return "tool";
  return "unknown";
}

const MAX_INCIDENTS = 64;

export function createPolicyGuard(options: PolicyGuardOptions): PolicyGuard {
  const violations: PolicyViolation[] = [];
  const notices: PolicyNotice[] = [];
  const incidents: PolicyIncident[] = [];
  const incidentIndex = new Map<string, PolicyIncident>();
  let droppedIncidents = 0;
  let totalIncidents = 0;
  let blockedCode: PolicyBlockCode | null = null;
  const maxToolResultChars = safeLimit(options.maxToolResultChars, DEFAULT_MAX_TOOL_RESULT_CHARS);
  const maxToolInputChars = safeLimit(options.maxToolInputChars, DEFAULT_MAX_TOOL_INPUT_CHARS);

  const incidentKey = (item: PolicyIncident): string =>
    [
      item.rule,
      item.stage,
      item.source,
      item.action,
      item.turnIndex ?? "",
      item.messageIndex ?? "",
      item.toolCallId ?? "",
    ].join("|");

  /**
   * Records one bounded diagnostic. Deduplication uses run-scoped coordinates,
   * never text or a text hash. Diagnostics are best-effort: a failure here must
   * never turn a would-be block into an allow.
   */
  const record = (item: PolicyIncident): void => {
    totalIncidents += 1;
    const key = incidentKey(item);
    const existing = incidentIndex.get(key);
    if (existing !== undefined) {
      existing.occurrences += 1;
    } else if (incidents.length < MAX_INCIDENTS) {
      incidentIndex.set(key, item);
      incidents.push(item);
    } else {
      droppedIncidents += 1;
      return;
    }
    try {
      options.onIncident?.(incidentIndex.get(key) ?? item);
    } catch {
      // Diagnostics must not influence the decision.
    }
  };

  const violation = (
    kind: ViolationKind,
    detail: string,
    locator: Partial<Pick<PolicyIncident, "rule" | "stage" | "source" | "turnIndex" | "messageIndex" | "toolCallId">> = {},
  ): void => {
    const item = { kind, detail } satisfies PolicyViolation;
    violations.push(item);
    const code = BLOCK_CODE_BY_VIOLATION[kind];
    const incident: PolicyIncident = {
      rule: locator.rule ?? kind,
      stage: locator.stage ?? "before_provider_request",
      source: locator.source ?? "payload",
      action: "block",
      occurrences: 1,
      ...(locator.turnIndex !== undefined ? { turnIndex: locator.turnIndex } : {}),
      ...(locator.messageIndex !== undefined ? { messageIndex: locator.messageIndex } : {}),
      ...(locator.toolCallId !== undefined ? { toolCallId: locator.toolCallId } : {}),
    };
    record(incident);
    const first = blockedCode === null;
    if (first) blockedCode = code;
    try {
      options.onViolation?.(item);
    } catch {
      // Logging/callback failure must not clear or replace the block.
    }
    if (first) {
      try {
        options.onBlock?.(incident, code);
      } catch {
        // Same: the block is already recorded.
      }
    }
  };

  const notice = (
    kind: NoticeKind,
    detail: string,
    locator: Partial<Pick<PolicyIncident, "rule" | "stage" | "source" | "turnIndex" | "messageIndex" | "toolCallId">> = {},
  ): void => {
    const item = { kind, detail } satisfies PolicyNotice;
    notices.push(item);
    record({
      rule: locator.rule ?? kind,
      stage: locator.stage ?? "before_provider_request",
      source: locator.source ?? "payload",
      action: kind === "pii_redacted" || kind === "sensitive_redacted" ? "sanitize" : "notice",
      occurrences: 1,
      ...(locator.turnIndex !== undefined ? { turnIndex: locator.turnIndex } : {}),
      ...(locator.messageIndex !== undefined ? { messageIndex: locator.messageIndex } : {}),
      ...(locator.toolCallId !== undefined ? { toolCallId: locator.toolCallId } : {}),
    });
    try {
      options.onNotice?.(item);
    } catch {
      // Diagnostics must not influence the decision.
    }
  };

  const blockError = (): Error => {
    const code = blockedCode ?? "POLICY_GUARD_BLOCKED";
    const error = new Error(`policy: blocked (${code})`) as Error & { errorCode: string };
    error.errorCode = code;
    return error;
  };

  /**
   * Final provider-boundary check. This is the only hook that can stop egress:
   * it runs from `onPayload`, before the HTTP request is built, and a throw
   * there is converted into a stream error without a request being sent. The
   * other hooks swallow throws, so they must never be the enforcement point.
   */
  const beforeProviderRequest = (payload: unknown): unknown => {
    // Sticky: once a run is hard-blocked it stays blocked, so SDK auto-retry
    // cannot launder the decision by re-invoking this hook.
    if (blockedCode !== null) throw blockError();

    const providerBlocked =
      (options.localRequired && options.providerKind !== "local") ||
      (options.cloudAllowed === false && options.providerKind !== "local");
    if (providerBlocked) {
      const localOnly = Boolean(options.localRequired);
      violation(
        localOnly ? "local_provider_required" : "cloud_provider_forbidden",
        localOnly
          ? "local-only context cannot be sent to a non-local provider"
          : "cloud provider use is disabled by policy",
        {
          rule: localOnly ? RULES.providerLocalRequired : RULES.providerCloudForbidden,
          stage: "before_provider_request",
          source: "payload",
        },
      );
    }
    if (containsSecret(payload, options.secrets)) {
      violation(
        "secret_in_provider_payload",
        "provider payload contained secret material; transport was blocked",
        { rule: RULES.secretPayload, stage: "before_provider_request", source: "payload" },
      );
    }
    if (containsMaskedContract(payload)) {
      violation("masked_data", "provider payload contained a masked-data contract field", {
        rule: RULES.maskedContract,
        stage: "before_provider_request",
        source: "payload",
      });
    }
    if (containsMaskedWording(payload)) {
      notice("masked_text", "provider payload mentioned masked wording", {
        rule: RULES.maskedText,
        stage: "before_provider_request",
        source: "payload",
      });
    }
    if (containsInjection(payload)) {
      notice("instruction_like_text", "provider payload contained instruction-like wording", {
        rule: RULES.injectionMarker,
        stage: "before_provider_request",
        source: "payload",
      });
    }
    if (containsPii(payload)) {
      notice("pii_redacted", "unnecessary PII was removed before provider transport", {
        rule: RULES.piiRedacted,
        stage: "before_provider_request",
        source: "payload",
      });
    }
    if (blockedCode !== null) throw blockError();
    return redactSensitive(payload, options.secrets);
  };

  const extension: InlineExtension = {
    name: "familygraph-policy-guard",
    hidden: true,
    factory: (pi) => {
      // input: cheap first-pass screening. Wording is a diagnostic signal, not
      // an authorization decision, so it never swallows the user's prompt; a
      // secret typed here is caught at the provider boundary instead.
      pi.on("input", (event) => {
        if (containsInjection(event.text)) {
          notice("instruction_like_text", "input contained instruction-like wording", {
            rule: RULES.injectionMarker,
            stage: "input",
            source: "user",
          });
        }
        return { action: "continue" };
      });

      // tool_call: only server-issued, registered domain tools may execute.
      pi.on("tool_call", (event) => {
        const canonicalName = canonicalPolicyToolName(event.toolName);
        const toolCallId = safeToolCallId(event.toolCallId);
        const locator = {
          stage: "tool_call" as const,
          source: "assistant" as const,
          ...(toolCallId !== undefined ? { toolCallId } : {}),
        };
        if (containsInjection(event.input)) {
          notice("instruction_like_text", "tool arguments contained instruction-like wording", {
            rule: RULES.injectionMarker,
            ...locator,
          });
        }
        const inputSize = serialized(event.input).length;
        if (inputSize > maxToolInputChars) {
          violation("unsafe_tool_arguments", "tool arguments exceeded the size limit", {
            rule: RULES.toolArgsTooLarge,
            ...locator,
          });
          return { block: true, reason: "policy: tool arguments too large", terminate: true };
        }
        if (containsScopeOverride(event.input)) {
          violation("unsafe_tool_arguments", "tool arguments attempted a scope override", {
            rule: RULES.scopeOverride,
            ...locator,
          });
          return { block: true, reason: "policy: unsafe tool arguments", terminate: true };
        }
        if (containsSecret(event.input, options.secrets)) {
          violation("unsafe_tool_arguments", "tool arguments contained secret material", {
            rule: RULES.secretToolArgs,
            ...locator,
          });
          return { block: true, reason: "policy: unsafe tool arguments", terminate: true };
        }
        if (!options.allowlist.has(canonicalName)) {
          violation("tool_not_allowed", `tool "${canonicalName}" is not in the run allowlist`, {
            rule: RULES.toolNotAllowed,
            ...locator,
          });
          return {
            block: true,
            reason: `policy: tool not allowed: ${canonicalName}`,
            terminate: true,
          };
        }
        return undefined;
      });

      // tool_result: bound output, redact sensitive values, and label facts
      // that have not reached confirmation before they re-enter context.
      pi.on("tool_result", (event) => {
        const toolCallId = safeToolCallId(event.toolCallId);
        const locator = {
          stage: "tool_result" as const,
          source: "tool" as const,
          ...(toolCallId !== undefined ? { toolCallId } : {}),
        };
        const safe = sanitizeToolResult(event.content, options.secrets, maxToolResultChars);
        if (safe.redacted) {
          notice("sensitive_redacted", "tool result contained redacted sensitive material", {
            rule: RULES.sensitiveRedacted,
            ...locator,
          });
        }
        if (safe.oversized) {
          violation("tool_result_too_large", "tool result exceeded the output limit", {
            rule: RULES.toolResultTooLarge,
            ...locator,
          });
        }
        if (safe.unconfirmed) {
          notice("unconfirmed_fact_annotated", "tool result was labeled as unconfirmed", {
            rule: RULES.unconfirmed,
            ...locator,
          });
        }
        if (safe.maskedWording) {
          notice("masked_text", "tool result mentioned masked wording", {
            rule: RULES.maskedText,
            ...locator,
          });
        }
        if (containsInjection(event.content)) {
          notice("instruction_like_text", "tool result contained instruction-like wording", {
            rule: RULES.injectionMarker,
            ...locator,
          });
        }
        if (safe.maskedContract) {
          violation("masked_data", "tool result carried a masked-data contract field", {
            rule: RULES.maskedContract,
            ...locator,
          });
        }
        // Return only when content actually changed. A notice-only pass must
        // leave the result untouched, so this handler stays pure for wording.
        if (safe.redacted || safe.oversized || safe.unconfirmed || safe.maskedContract) {
          return {
            content: safe.content as typeof event.content,
            isError: safe.oversized || safe.maskedContract,
          };
        }
        return undefined;
      });

      // context: observation only, deliberately no message rewriting.
      //
      // Rewriting SDK messages here would risk breaking tool-call/result pairing
      // and invalidating opaque (signed) thinking blocks, for no security gain:
      // the authoritative egress control is `beforeProviderRequest`, which
      // redacts the fully assembled payload and preserves numeric token caps.
      // Keyword hits are recorded as bounded diagnostics and change nothing.
      pi.on("context", (event) => {
        event.messages.forEach((message, messageIndex) => {
          const source = sourceFromRole((message as { role?: unknown }).role);
          if (containsInjection(message)) {
            notice("instruction_like_text", "context contained instruction-like wording", {
              rule: RULES.injectionMarker,
              stage: "context",
              source,
              messageIndex,
            });
          }
          if (containsMaskedWording(message)) {
            notice("masked_text", "context mentioned masked wording", {
              rule: RULES.maskedText,
              stage: "context",
              source,
              messageIndex,
            });
          }
          if (containsMaskedContract(message)) {
            violation("masked_data", "context carried a masked-data contract field", {
              rule: RULES.maskedContract,
              stage: "context",
              source,
              messageIndex,
            });
          }
        });
        return undefined;
      });

      // Registered for completeness; the effective egress check runs from
      // `onPayload` in session.ts, because this runner swallows handler throws.
      pi.on("before_provider_request", (event) => beforeProviderRequest(event.payload));

      // This catches unknown tools that Pi rejects before tool_call can run.
      pi.on("tool_execution_end", (event) => {
        const canonicalName = canonicalPolicyToolName(event.toolName);
        if (!options.allowlist.has(canonicalName)) {
          violation("tool_not_allowed", `attempted tool "${canonicalName}" is outside the run allowlist`, {
            rule: RULES.toolNotAllowed,
            stage: "tool_execution_end",
            source: "assistant",
            ...(safeToolCallId(event.toolCallId) !== undefined
              ? { toolCallId: safeToolCallId(event.toolCallId) as string }
              : {}),
          });
        }
      });

      // Reliable terminal signal for audit/settle projection. Only the event
      // type is forwarded, so hidden message content and usage never escape.
      pi.on("agent_settled", () => {
        options.onSettled?.({ type: "agent_settled" });
      });
    },
  };

  return {
    extension,
    get violationCount(): number {
      return violations.length;
    },
    get blockingViolationCount(): number {
      return violations.length;
    },
    get violations(): readonly PolicyViolation[] {
      return violations;
    },
    get notices(): readonly PolicyNotice[] {
      return notices;
    },
    get incidents(): readonly PolicyIncident[] {
      return incidents;
    },
    get incidentTotal(): number {
      return totalIncidents;
    },
    get incidentsDropped(): number {
      return droppedIncidents;
    },
    get blocked(): boolean {
      return blockedCode !== null;
    },
    get blockCode(): PolicyBlockCode | null {
      return blockedCode;
    },
    beforeProviderRequest,
  };
}
