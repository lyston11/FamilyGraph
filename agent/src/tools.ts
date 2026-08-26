/**
 * FamilyGraph domain tool declarations + executor bridge.
 *
 * The model sees only these tool declarations; every execution is proxied
 * through the FastAPI internal execute endpoint, which re-checks identity,
 * VisibilityPolicy, allowlist and idempotency. V2.1 ships exactly two
 * read-only skeleton tools — real business tools arrive in V2.2/V2.3.
 */

import { Type, type Static } from "typebox";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";

export const TOOL_VERSIONS = {
  "familygraph.echo": 1,
  "familygraph.probe_scope": 1,
} as const;

export type DomainToolName = keyof typeof TOOL_VERSIONS;

/** Names of the tools this sidecar may register, derived from the registry. */
export function defaultToolNames(): string[] {
  return Object.keys(TOOL_VERSIONS);
}

/** Execution bridge — implemented by InternalClient in production. */
export type DomainToolExecutor = (
  toolName: DomainToolName,
  call: { tool_call_id: string; version: number; input: unknown },
) => Promise<{ ok: true; result: unknown }>;

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

const EchoSchema = Type.Object({
  text: Type.String({ description: "Text echoed back unchanged." }),
});

const ProbeScopeSchema = Type.Object({});

function textResult(text: string): { content: Array<{ type: "text"; text: string }>; details: unknown } {
  return { content: [{ type: "text", text }], details: undefined };
}

/**
 * Build the Pi custom tool definitions. `toolCallId` comes from Pi and is
 * forwarded verbatim for audit traceability (server-side side-effect dedupe
 * is a V2.4 deliverable; V2.1 tools must stay read-only).
 */
export function createDomainTools(executor: DomainToolExecutor): ToolDefinition[] {
  const echo: ToolDefinition<typeof EchoSchema> = {
    name: "familygraph.echo",
    label: "Echo",
    description:
      "Echoes the given text back unchanged. Read-only diagnostic tool provided by FamilyGraph.",
    parameters: EchoSchema,
    execute: async (toolCallId, params: Static<typeof EchoSchema>) => {
      try {
        const outcome = await executor("familygraph.echo", {
          tool_call_id: toolCallId,
          version: TOOL_VERSIONS["familygraph.echo"],
          input: { text: params.text },
        });
        // Backend echo output contract: {text} (backend/app/services/agent_tools.py).
        const result = outcome.result as { text?: unknown } | undefined;
        const echoed = typeof result?.text === "string" ? result.text : params.text;
        return textResult(echoed);
      } catch (error) {
        throw new Error(`familygraph.echo failed: ${toErrorMessage(error)}`);
      }
    },
  };

  const probeScope: ToolDefinition<typeof ProbeScopeSchema> = {
    name: "familygraph.probe_scope",
    label: "Probe scope",
    description:
      "Returns a summary of the current run's authorization scope (actor, space, agent kind). Read-only.",
    parameters: ProbeScopeSchema,
    execute: async (toolCallId) => {
      try {
        const outcome = await executor("familygraph.probe_scope", {
          tool_call_id: toolCallId,
          version: TOOL_VERSIONS["familygraph.probe_scope"],
          input: {},
        });
        const result = outcome.result as Record<string, unknown> | undefined;
        return textResult(JSON.stringify(result ?? {}));
      } catch (error) {
        throw new Error(`familygraph.probe_scope failed: ${toErrorMessage(error)}`);
      }
    },
  };

  return [echo, probeScope] as unknown as ToolDefinition[];
}
