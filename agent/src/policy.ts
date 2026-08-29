/**
 * familygraph-policy-guard — the synchronous model-boundary policy barrier.
 *
 * This extension is deliberately lightweight and has no network/database
 * access. FastAPI remains authoritative for data and tool authorization.
 */

import type { InlineExtension } from "@earendil-works/pi-coding-agent";

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

type ViolationKind =
  | "unsafe_input"
  | "prompt_injection"
  | "tool_not_allowed"
  | "unsafe_tool_arguments"
  | "tool_result_redacted"
  | "masked_data"
  | "tool_result_too_large"
  | "local_provider_required"
  | "cloud_provider_forbidden"
  | "secret_in_provider_payload";

type NoticeKind =
  | "sensitive_redacted"
  | "pii_redacted"
  | "unconfirmed_fact_annotated";

const BLOCKING_VIOLATION_KINDS = new Set<ViolationKind>([
  "unsafe_input",
  "prompt_injection",
  "tool_not_allowed",
  "unsafe_tool_arguments",
  "masked_data",
  "tool_result_too_large",
  "local_provider_required",
  "cloud_provider_forbidden",
  "secret_in_provider_payload",
]);

export interface PolicyViolation {
  kind: ViolationKind;
  detail: string;
}

export interface PolicyNotice {
  kind: NoticeKind;
  detail: string;
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
  onSettled?: (event: { type: "agent_settled" }) => void;
}

export interface PolicyGuard {
  readonly extension: InlineExtension;
  readonly violationCount: number;
  readonly blockingViolationCount: number;
  readonly violations: readonly PolicyViolation[];
  readonly notices: readonly PolicyNotice[];
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
      out[key] = isCredentialKey(key)
        ? "[REDACTED]"
        : redactSecrets(item, secrets);
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

function containsInjection(value: unknown): boolean {
  const text = normalizedText(value);
  return INJECTION_MARKERS.some((marker) => text.includes(marker));
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
    ([key, item]) => SCOPE_KEYS.has(key.toLowerCase().replaceAll("-", "_")) || containsScopeOverride(item),
  );
}

function isUnconfirmed(value: unknown): boolean {
  if (typeof value === "string") {
    if (/\b(?:unconfirmed|pending|proposed|disputed)\b|未经确认|待确认|有争议/i.test(value)) return true;
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

function containsMaskedData(value: unknown): boolean {
  if (typeof value === "string") {
    return /\bmasked\b|遮罩|已脱敏/i.test(value);
  }
  if (Array.isArray(value)) return value.some(containsMaskedData);
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value as Record<string, unknown>).some(([key, item]) => {
    const normalizedKey = key.toLowerCase().replaceAll("-", "_");
    return (
      (normalizedKey === "visibility" && String(item).toLowerCase() === "masked") ||
      (normalizedKey === "masked" && item === true) ||
      containsMaskedData(item)
    );
  });
}

function annotateUnconfirmed(value: unknown): unknown {
  const label = unconfirmedLabel();
  if (Array.isArray(value)) return [label, ...value];
  return [label, { type: "text", text: serialized(value) }];
}

function annotateUnconfirmedMessage(value: unknown): unknown {
  if (value !== null && typeof value === "object" && "content" in value) {
    return {
      ...(value as Record<string, unknown>),
      content: annotateUnconfirmed((value as { content: unknown }).content),
    };
  }
  return annotateUnconfirmed(value);
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

function sanitizeToolResult(
  content: unknown,
  secrets: readonly string[],
  maxChars: number,
): {
  content: unknown;
  redacted: boolean;
  oversized: boolean;
  unconfirmed: boolean;
  injection: boolean;
  masked: boolean;
} {
  const masked = containsMaskedData(content);
  if (masked) {
    return {
      content: [{ type: "text", text: "[FamilyGraph masked data blocked by policy]" }],
      redacted: false,
      oversized: false,
      unconfirmed: false,
      injection: false,
      masked: true,
    };
  }
  const sanitized = redactSensitive(content, secrets);
  const redacted = serialized(sanitized) !== serialized(content);
  const unconfirmed = isUnconfirmed(content);
  const injection = containsInjection(content);
  if (injection) {
    return {
      content: [{ type: "text", text: "[FamilyGraph data blocked by policy]" }],
      redacted: false,
      oversized: false,
      unconfirmed,
      injection,
      masked: false,
    };
  }
  const annotated = unconfirmed ? annotateUnconfirmed(sanitized) : sanitized;
  const raw = serialized(annotated);
  if (raw.length <= maxChars) {
    return {
      content: annotated,
      redacted,
      oversized: false,
      unconfirmed,
      injection: false,
      masked: false,
    };
  }
  const bounded = boundedResultText(raw, maxChars, unconfirmed);
  return {
    content: [{ type: "text", text: bounded }],
    redacted,
    oversized: true,
    unconfirmed,
    injection: false,
    masked: false,
  };
}

function violation(
  violations: PolicyViolation[],
  options: PolicyGuardOptions,
  kind: ViolationKind,
  detail: string,
): void {
  const item = { kind, detail } satisfies PolicyViolation;
  violations.push(item);
  options.onViolation?.(item);
}

function notice(notices: PolicyNotice[], options: PolicyGuardOptions, kind: NoticeKind, detail: string): void {
  const item = { kind, detail } satisfies PolicyNotice;
  notices.push(item);
  options.onNotice?.(item);
}

export function createPolicyGuard(options: PolicyGuardOptions): PolicyGuard {
  const violations: PolicyViolation[] = [];
  const notices: PolicyNotice[] = [];
  const maxToolResultChars = safeLimit(options.maxToolResultChars, DEFAULT_MAX_TOOL_RESULT_CHARS);
  const maxToolInputChars = safeLimit(options.maxToolInputChars, DEFAULT_MAX_TOOL_INPUT_CHARS);
  const beforeProviderRequest = (payload: unknown): unknown => {
    const hasSecret = containsSecret(payload, options.secrets);
    const hasMaskedData = containsMaskedData(payload);
    const hasPii = containsPii(payload);
    const providerBlocked =
      (options.localRequired && options.providerKind !== "local") ||
      (options.cloudAllowed === false && options.providerKind !== "local");
    if (providerBlocked) {
      violation(
        violations,
        options,
        options.localRequired ? "local_provider_required" : "cloud_provider_forbidden",
        options.localRequired
          ? "local-only context cannot be sent to a non-local provider"
          : "cloud provider use is disabled by policy",
      );
    }
    if (hasSecret) {
      violation(
        violations,
        options,
        "secret_in_provider_payload",
        "provider payload contained secret material; transport was blocked",
      );
    }
    if (hasMaskedData) {
      violation(violations, options, "masked_data", "provider payload contained masked data");
    }
    if (hasPii) {
      notice(notices, options, "pii_redacted", "unnecessary PII was removed before provider transport");
    }
    if (providerBlocked || hasMaskedData || hasSecret) {
      const error = new Error(
        providerBlocked
          ? "policy: provider blocked by local/cloud data policy"
          : hasMaskedData
            ? "policy: masked data cannot reach provider"
            : "policy: secret material cannot reach provider",
      ) as Error & { errorCode: string };
      error.errorCode = providerBlocked
        ? "POLICY_PROVIDER_BLOCKED"
        : hasMaskedData
          ? "POLICY_MASKED_DATA"
          : "POLICY_SECRET_IN_PROVIDER_PAYLOAD";
      throw error;
    }
    return redactSensitive(payload, options.secrets);
  };

  const extension: InlineExtension = {
    name: "familygraph-policy-guard",
    hidden: true,
    factory: (pi) => {
      // input: cheap first-pass screening before prompt expansion.
      pi.on("input", (event) => {
        if (containsInjection(event.text) || containsSecret(event.text, options.secrets)) {
          violation(violations, options, "unsafe_input", "input contains unsafe or secret material");
          return { action: "handled" };
        }
        return { action: "continue" };
      });

      // tool_call: only server-issued, registered domain tools may execute.
      pi.on("tool_call", (event) => {
        const inputSize = serialized(event.input).length;
        if (
          inputSize > maxToolInputChars ||
          containsScopeOverride(event.input) ||
          containsInjection(event.input) ||
          containsSecret(event.input, options.secrets)
        ) {
          violation(
            violations,
            options,
            "unsafe_tool_arguments",
            "tool call contained oversized, scope-overriding, instruction-like, or secret arguments",
          );
          return {
            block: true,
            reason: "policy: unsafe tool arguments",
            terminate: true,
          };
        }
        if (!options.allowlist.has(event.toolName)) {
          violation(
            violations,
            options,
            "tool_not_allowed",
            `tool "${event.toolName}" is not in the run allowlist`,
          );
          return {
            block: true,
            reason: `policy: tool not allowed: ${event.toolName}`,
            terminate: true,
          };
        }
        return undefined;
      });

      // tool_result: bound output, redact sensitive values, and label facts
      // that have not reached confirmation before they re-enter context.
      pi.on("tool_result", (event) => {
        const safe = sanitizeToolResult(event.content, options.secrets, maxToolResultChars);
        if (safe.redacted) {
          notice(notices, options, "sensitive_redacted", "tool result contained redacted sensitive material");
        }
        if (safe.oversized) {
          violation(violations, options, "tool_result_too_large", "tool result exceeded the output limit");
        }
        if (safe.unconfirmed) {
          notice(notices, options, "unconfirmed_fact_annotated", "tool result was labeled as unconfirmed");
        }
        if (safe.injection) {
          violation(violations, options, "prompt_injection", "tool result contained an instruction-like data block");
        }
        if (safe.masked) {
          violation(violations, options, "masked_data", "tool result contained masked data");
        }
        if (safe.redacted || safe.oversized || safe.unconfirmed || safe.injection || safe.masked) {
          return {
            content: safe.content as typeof event.content,
            isError: safe.oversized || safe.injection || safe.masked,
          };
        }
        return undefined;
      });

      // context: only filter/sanitize the prefetched context and messages;
      // this hook deliberately performs no database or network work.
      pi.on("context", (event) => {
        let changed = false;
        const safeMessages = event.messages.flatMap((message) => {
          if (containsInjection(message)) {
            violation(violations, options, "prompt_injection", "context contained an instruction-like data block");
            changed = true;
            return [];
          }
          if (containsMaskedData(message)) {
            violation(violations, options, "masked_data", "context contained masked data");
            changed = true;
            return [];
          }
          const unconfirmed = isUnconfirmed(message);
          const safe = redactSensitive(message, options.secrets);
          const annotated = unconfirmed ? annotateUnconfirmedMessage(safe) : safe;
          if (unconfirmed) {
            notice(notices, options, "unconfirmed_fact_annotated", "context contained an unconfirmed fact");
            changed = true;
          }
          if (serialized(annotated) !== serialized(message)) {
            notice(notices, options, "pii_redacted", "context contained redacted sensitive material");
            changed = true;
          }
          return [annotated as typeof message];
        });
        return changed ? { messages: safeMessages } : undefined;
      });

      // Final payload check. Secrets are never sent; unnecessary PII is
      // removed and reported as a non-blocking redaction notice.
      pi.on("before_provider_request", (event) => beforeProviderRequest(event.payload));

      // This catches unknown tools that Pi rejects before tool_call can run.
      pi.on("tool_execution_end", (event) => {
        if (!options.allowlist.has(event.toolName)) {
          violation(
            violations,
            options,
            "tool_not_allowed",
            `attempted tool "${event.toolName}" is outside the run allowlist`,
          );
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
      return violations.filter((item) => BLOCKING_VIOLATION_KINDS.has(item.kind)).length;
    },
    get violations(): readonly PolicyViolation[] {
      return violations;
    },
    get notices(): readonly PolicyNotice[] {
      return notices;
    },
    beforeProviderRequest,
  };
}
