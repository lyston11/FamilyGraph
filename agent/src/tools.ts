/**
 * FamilyGraph domain tool declarations + executor bridge.
 *
 * The model sees only these tool declarations; every execution is proxied
 * through the FastAPI internal execute endpoint, which re-checks identity,
 * VisibilityPolicy, allowlist/min_kind and idempotency. The actual availability
 * per run is governed exclusively by the server-issued tool_allowlist.
 *
 * Registry contents:
 *  - V2.1 skeleton diagnostics: echo, probe_scope;
 *  - V2.2 single-space read-only assistant query tools (shared contract with
 *    the backend registry): get_self_context, list_visible_people,
 *    get_profile_summary, search_space, get_relationship_path,
 *    explain_structural_path — outputs are visibility-projected, so the model
 *    never sees masked raw values;
 *  - steward_ping: declared so steward runs can build a session (fixes the
 *    V2.1 latent mismatch); its execution still goes through the execute
 *    endpoint, where min_kind rejects it in assistant runs.
 *
 * No business write tool may be registered here until the server-side
 * side-effect dedupe table exists (V2.4).
 */

import { Type, type Static } from "typebox";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";

export const TOOL_VERSIONS = {
  "familygraph.echo": 1,
  "familygraph.probe_scope": 1,
  "familygraph.steward_ping": 1,
  "familygraph.get_self_context": 1,
  "familygraph.list_visible_people": 1,
  "familygraph.get_profile_summary": 1,
  "familygraph.search_space": 1,
  "familygraph.get_relationship_path": 1,
  "familygraph.explain_structural_path": 1,
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

const StewardPingSchema = Type.Object({});

const GetSelfContextSchema = Type.Object({});

const ListVisiblePeopleSchema = Type.Object({
  query: Type.Optional(
    Type.String({ description: "可选的姓名关键词，用于过滤可见人物列表。" }),
  ),
  limit: Type.Optional(Type.Integer({ description: "可选的单次返回数量上限。" })),
  cursor: Type.Optional(
    Type.Integer({ description: "可选的分页游标（offset 语义），取自上一次结果返回的 next_cursor。" }),
  ),
});

const GetProfileSummarySchema = Type.Object({
  user_id: Type.Integer({
    description: "目标人物的用户 ID（必须是当前空间内可能可见的人物）。",
  }),
});

const SearchSpaceSchema = Type.Object({
  query: Type.String({ description: "搜索关键词（人物姓名等）。" }),
  limit: Type.Optional(Type.Integer({ description: "可选的单次返回数量上限。" })),
});

const RelationshipPathSchema = Type.Object({
  to_user_id: Type.Integer({ description: "目标人物的用户 ID。" }),
  from_user_id: Type.Optional(
    Type.Integer({ description: "可选的起点用户 ID；省略时以当前用户为起点。" }),
  ),
});

function textResult(text: string): { content: Array<{ type: "text"; text: string }>; details: unknown } {
  return { content: [{ type: "text", text }], details: undefined };
}

/** Drop unset optional fields so forwarded inputs never exceed the contract. */
function compactInput(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined));
}

/**
 * Shared read-only query path: proxy through FastAPI and surface the projected
 * JSON output as text. Errors (including domain codes such as
 * FG_PROFILE_NOT_AVAILABLE) surface to the model as errored tool results.
 */
async function queryViaExecutor(
  executor: DomainToolExecutor,
  toolName: DomainToolName,
  toolCallId: string,
  input: Record<string, unknown>,
): Promise<{ content: Array<{ type: "text"; text: string }>; details: unknown }> {
  try {
    const outcome = await executor(toolName, {
      tool_call_id: toolCallId,
      version: TOOL_VERSIONS[toolName],
      input,
    });
    return textResult(JSON.stringify(outcome.result ?? {}));
  } catch (error) {
    throw new Error(`${toolName} failed: ${toErrorMessage(error)}`);
  }
}

/**
 * Build the Pi custom tool definitions. `toolCallId` comes from Pi and is
 * forwarded verbatim for audit traceability (server-side side-effect dedupe
 * is a V2.4 deliverable; every registered tool stays strictly read-only).
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

  const stewardPing: ToolDefinition<typeof StewardPingSchema> = {
    name: "familygraph.steward_ping",
    label: "Steward ping",
    description:
      "steward 运行专用的链路探针：验证 sidecar 到 FastAPI 的工具执行通路（assistant 运行中服务端会按 min_kind 拒绝调用）。只读诊断工具，无任何副作用。",
    parameters: StewardPingSchema,
    execute: async (toolCallId) =>
      queryViaExecutor(executor, "familygraph.steward_ping", toolCallId, {}),
  };

  const getSelfContext: ToolDefinition<typeof GetSelfContextSchema> = {
    name: "familygraph.get_self_context",
    label: "Get self context",
    description:
      "获取当前运行范围的摘要：当前用户本人的身份投影与所在空间的概览。回答关于「我」或「当前空间」的问题前应先调用本工具。只读工具，不修改任何数据。",
    parameters: GetSelfContextSchema,
    execute: async (toolCallId) =>
      queryViaExecutor(executor, "familygraph.get_self_context", toolCallId, {}),
  };

  const listVisiblePeople: ToolDefinition<typeof ListVisiblePeopleSchema> = {
    name: "familygraph.list_visible_people",
    label: "List visible people",
    description:
      "列出当前空间内当前用户可见的人物，可按姓名关键词过滤并支持 limit/cursor 分页；仅返回经过可见性策略投影的字段。只读工具。",
    parameters: ListVisiblePeopleSchema,
    execute: async (toolCallId, params: Static<typeof ListVisiblePeopleSchema>) =>
      queryViaExecutor(executor, "familygraph.list_visible_people", toolCallId, compactInput({ ...params })),
  };

  const getProfileSummary: ToolDefinition<typeof GetProfileSummarySchema> = {
    name: "familygraph.get_profile_summary",
    label: "Get profile summary",
    description:
      "读取指定人物的可见档案摘要投影。若目标人物不可见或不存在，将返回错误码 FG_PROFILE_NOT_AVAILABLE，此时应向用户说明资料不足。只读工具。",
    parameters: GetProfileSummarySchema,
    execute: async (toolCallId, params: Static<typeof GetProfileSummarySchema>) =>
      queryViaExecutor(executor, "familygraph.get_profile_summary", toolCallId, {
        user_id: params.user_id,
      }),
  };

  const searchSpace: ToolDefinition<typeof SearchSpaceSchema> = {
    name: "familygraph.search_space",
    label: "Search space",
    description:
      "在当前空间范围内按关键词搜索当前用户可见的人物，结果已经过可见性策略过滤。只读工具。",
    parameters: SearchSpaceSchema,
    execute: async (toolCallId, params: Static<typeof SearchSpaceSchema>) =>
      queryViaExecutor(executor, "familygraph.search_space", toolCallId, compactInput({ ...params })),
  };

  const getRelationshipPath: ToolDefinition<typeof RelationshipPathSchema> = {
    name: "familygraph.get_relationship_path",
    label: "Get relationship path",
    description:
      "查询当前用户（或指定 from_user_id）与目标人物之间已确定的可见关系路径。若无可见路径应向用户说明资料不足。只读工具。",
    parameters: RelationshipPathSchema,
    execute: async (toolCallId, params: Static<typeof RelationshipPathSchema>) =>
      queryViaExecutor(executor, "familygraph.get_relationship_path", toolCallId, compactInput({ ...params })),
  };

  const explainStructuralPath: ToolDefinition<typeof RelationshipPathSchema> = {
    name: "familygraph.explain_structural_path",
    label: "Explain structural path",
    description:
      "解释两位人物之间已确定结构路径的逐跳依据：路径中每一步的关系类型与方向。仅解释已确定的结构路径，不做地方称谓推断。只读工具。",
    parameters: RelationshipPathSchema,
    execute: async (toolCallId, params: Static<typeof RelationshipPathSchema>) =>
      queryViaExecutor(executor, "familygraph.explain_structural_path", toolCallId, compactInput({ ...params })),
  };

  return [
    echo,
    probeScope,
    stewardPing,
    getSelfContext,
    listVisiblePeople,
    getProfileSummary,
    searchSpace,
    getRelationshipPath,
    explainStructuralPath,
  ] as unknown as ToolDefinition[];
}
