/**
 * familygraph-policy-guard — inline Pi extension (pi.on decision channel).
 *
 * Fail-closed enforcement inside the model loop:
 *  1. tool_call: the tool must be in the run's allowlist, otherwise the call
 *     is blocked and a violation is recorded (surfaced as run.failed with
 *     error_code POLICY_TOOL_BLOCKED; FastAPI writes the security audit).
 *  2. before_provider_request: the final provider payload is scanned for
 *     known secret material (service secret, provider API keys). Matches are
 *     redacted in place and recorded; the run is failed at settle time.
 *
 * Pi Guard passing does NOT authorize anything — FastAPI re-executes every
 * identity/policy check behind the execute endpoint.
 */

import type { InlineExtension } from "@earendil-works/pi-coding-agent";

export interface PolicyViolation {
  kind: "tool_not_allowed" | "secret_in_provider_payload";
  detail: string;
}

export interface PolicyGuardOptions {
  /** tool names allowed for this run (from the context projection). */
  allowlist: ReadonlySet<string>;
  /** Secret strings that must never appear in provider payloads. */
  secrets: readonly string[];
  onViolation?: (violation: PolicyViolation) => void;
}

export interface PolicyGuard {
  readonly extension: InlineExtension;
  readonly violationCount: number;
  /** All violations observed during this run, in order. */
  readonly violations: readonly PolicyViolation[];
}

/** Recursively redacts secret occurrences inside string leaves. */
export function redactSecrets(value: unknown, secrets: readonly string[]): unknown {
  if (typeof value === "string") {
    let out = value;
    for (const secret of secrets) {
      if (secret.length > 0 && out.includes(secret)) {
        out = out.split(secret).join("[REDACTED]");
      }
    }
    return out;
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactSecrets(item, secrets));
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      // Never serialize key material fields even when values differ.
      out[key] = /api[-_]?key|authorization|secret|password/i.test(key)
        ? "[REDACTED]"
        : redactSecrets(item, secrets);
    }
    return out;
  }
  return value;
}

export function createPolicyGuard(options: PolicyGuardOptions): PolicyGuard {
  const violations: PolicyViolation[] = [];
  const extension: InlineExtension = {
    name: "familygraph-policy-guard",
    hidden: true,
    factory: (pi) => {
      pi.on("tool_call", (event) => {
        // Only allowlisted custom domain tools exist in this process; anything
        // else means an unknown/coding tool surfaced somehow — block it.
        if (!options.allowlist.has(event.toolName)) {
          violations.push({
            kind: "tool_not_allowed",
            detail: `tool "${event.toolName}" is not in the run allowlist`,
          });
          options.onViolation?.(violations[violations.length - 1]!);
          return {
            block: true,
            reason: `policy: tool not allowed: ${event.toolName}`,
            terminate: true,
          };
        }
        return undefined;
      });

      pi.on("before_provider_request", (event) => {
        let leaked = false;
        const serialized = JSON.stringify(event.payload ?? null);
        for (const secret of options.secrets) {
          if (secret.length > 0 && serialized.includes(secret)) leaked = true;
        }
        if (leaked) {
          violations.push({
            kind: "secret_in_provider_payload",
            detail: "provider payload contained secret material (redacted)",
          });
          options.onViolation?.(violations[violations.length - 1]!);
        }
        return redactSecrets(event.payload, options.secrets);
      });

      // Unknown/non-registered tool names are short-circuited by Pi core
      // ("Tool not found") BEFORE the tool_call hook runs, so they are
      // observed here: attempted but never executed, always fail-closed.
      pi.on("tool_execution_end", (event) => {
        if (!options.allowlist.has(event.toolName)) {
          violations.push({
            kind: "tool_not_allowed",
            detail: `attempted tool "${event.toolName}" is outside the run allowlist`,
          });
          options.onViolation?.(violations[violations.length - 1]!);
        }
      });
    },
  };
  return {
    extension,
    get violationCount(): number {
      return violations.length;
    },
    get violations(): readonly PolicyViolation[] {
      return violations;
    },
  };
}
