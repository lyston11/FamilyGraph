/**
 * Pi session construction for one leased run.
 *
 * Security posture:
 *  - noTools:"all" + explicit tool allowlist → no coding tools ever exist;
 *  - custom domain tools execute exclusively through FastAPI;
 *  - the policy-guard inline extension blocks unknown tools and scans
 *    provider payloads for secret material;
 *  - providers are registered programmatically from sidecar env config
 *    (baseUrl/apiKey stay in process memory, never in payloads/logs);
 *  - session/settings managers are in-memory: no state survives the run and
 *    nothing is written to disk (crash recovery is owned by FastAPI).
 */

import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  createAgentSession,
  DefaultResourceLoader,
  type InlineExtension,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { streamSimple as openAIStreamSimple } from "@earendil-works/pi-ai/api/openai-completions";
import type {
  Api,
  AssistantMessageEventStream,
  Context,
  Model,
  SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { AgentConfig } from "./config.js";
import { RunEventBuffer } from "./events.js";
import { createPolicyGuard, type PolicyGuard } from "./policy.js";
import { ASSISTANT_SYSTEM_PROMPT } from "./prompt.js";
import { createDomainTools, defaultToolNames, type DomainToolName } from "./tools.js";
import type { InternalClient, RunContextProjection } from "./client.js";

/** Raised when provider policy makes this run unexecutable (explainable refusal). */
export class ProviderPolicyError extends Error {
  constructor(
    readonly errorCode:
      | "PROVIDER_DENIED"
      | "PROVIDER_DENIED_NO_LOCAL"
      | "PROVIDER_DENIED_CLOUD_FORBIDDEN"
      | "PROVIDER_UNRESOLVED",
    message: string,
  ) {
    super(message);
    this.name = new.target.name;
  }
}

export interface SessionBundle {
  session: Awaited<ReturnType<typeof createAgentSession>>["session"];
  events: RunEventBuffer;
  policyGuard: PolicyGuard;
}

export interface BuildSessionDeps {
  /** Test seam: override the registered provider's stream implementation. */
  streamOverride?: (
    model: Model<"openai-completions">,
    context: Context,
    options?: SimpleStreamOptions,
  ) => AssistantMessageEventStream;
  agentDir?: string;
  /** Returns true when the run must stop issuing tool calls (cancel/lease lost). */
  shouldStopToolCalls?: () => boolean;
}

const TOKEN_CAP_KEYS = new Set(["max_tokens", "max_completion_tokens", "maxTokens"]);

/**
 * Ensure any token-cap field that survives provider-body re-serialization is an
 * integer. Some openai-compatible relays (Go GeneralOpenAIRequest) reject string
 * token caps with an unmarshal error, so the sidecar guarantees the wire type.
 * This is defense-in-depth on top of policy.ts preserving such fields.
 */
function coerceTokenCaps(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(coerceTokenCaps);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (TOKEN_CAP_KEYS.has(key) && item !== undefined && item !== null) {
        const asNumber = Number(item);
        out[key] = Number.isFinite(asNumber) && asNumber > 0 ? Math.floor(asNumber) : item;
      } else {
        out[key] = coerceTokenCaps(item);
      }
    }
    return out;
  }
  return value;
}

function buildModelLiteral(
  providerId: string,
  modelId: string,
  baseUrl: string,
): Model<"openai-completions"> {
  return {
    id: modelId,
    name: modelId,
    api: "openai-completions",
    provider: providerId,
    baseUrl,
    reasoning: true,
    input: ["text", "image"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 200_000,
    maxTokens: 8192,
    // openai-compatible 中转（含 zhipu glm 系）：发送 max_tokens 而非
    // max_completion_tokens，避免中转 Go 网关按 string 拒绝 uint token 上限；
    // 其余 compat 交由 pi-ai 依 baseUrl 自动探测（与用户 pi 的 guga 配置一致）。
    compat: { maxTokensField: "max_tokens" },
  };
}

export function resolveProvider(
  config: AgentConfig,
  provider: RunContextProjection["provider"],
  runToken: string,
): { entry: { kind: "openai_compatible" | "local"; baseUrl: string; apiKey: string | undefined; model: string }; modelId: string; providerId: string } {
  if (provider === null || provider.policy_result !== "allowed") {
    // Explainable refusals for policy_result !== "allowed" are handled by the
    // worker BEFORE the model loop starts; reaching here is a wiring error.
    throw new ProviderPolicyError("PROVIDER_UNRESOLVED", "provider policy did not allow this run");
  }
  if (provider.kind !== "local" && provider.kind !== "openai_compatible") {
    throw new ProviderPolicyError(
      "PROVIDER_UNRESOLVED",
      `provider "${provider.provider_id}" has no supported kind`,
    );
  }
  // P1 唯一 egress：server 下发的 base_url 是 internal 代理路径（"/internal/..."），
  // 真实 Provider base_url/api_key 不出服务端；sidecar 把模型请求指向代理端点，
  // 以 run token 作为 Bearer 凭据，代理在服务端重新解密并转发。
  const rawBaseUrl = provider.base_url;
  if (provider.model === null || rawBaseUrl === null || rawBaseUrl === "") {
    throw new ProviderPolicyError(
      "PROVIDER_UNRESOLVED",
      `no runtime config matches provider "${provider.provider_id}"`,
    );
  }
  let baseUrl: string;
  let apiKey: string | undefined;
  if (rawBaseUrl.startsWith("/")) {
    baseUrl = new URL(rawBaseUrl, config.internalApiBaseUrl).toString().replace(/\/$/, "");
    apiKey = runToken;
  } else {
    // 兼容路径：直接下发完整 base_url（本地 Provider 场景）；api_key 仍来自 projection
    baseUrl = rawBaseUrl;
    apiKey = provider.api_key ?? undefined;
  }
  return {
    entry: { kind: provider.kind, baseUrl, apiKey, model: provider.model },
    modelId: provider.model,
    providerId: provider.provider_id || "familygraph-provider",
  };
}

/**
 * Build one Pi AgentSession bound to the run context projection.
 * The caller owns subscribing, prompt()ing and aborting the session.
 */
export async function buildRunSession(
  config: AgentConfig,
  client: InternalClient,
  projection: RunContextProjection,
  runToken: string,
  deps: BuildSessionDeps = {},
): Promise<SessionBundle> {
  if (
    projection.tool_allowlist.length === 0 ||
    !projection.tool_allowlist.every((name) => defaultToolNames().includes(name))
  ) {
    throw new Error(
      `context allowlist contains tools outside the sidecar registry: ${projection.tool_allowlist.join(", ")}`,
    );
  }

  const { entry, modelId, providerId } = resolveProvider(config, projection.provider, runToken);
  const model = buildModelLiteral(providerId, modelId, entry.baseUrl);

  // Fully offline model/auth runtime: no catalog refresh, no network probing,
  // credentials stored under an ephemeral scratch dir (never ~/.pi).
  const agentDir = deps.agentDir ?? mkdtempSync(join(tmpdir(), "fg-agent-run-"));
  const modelRuntime = await ModelRuntime.create({
    authPath: join(agentDir, "auth.json"),
    modelsPath: null,
    refreshOnCreate: false,
  });

  const secrets = [config.serviceSecret, ...(entry.apiKey ? [entry.apiKey] : [])];

  const policyGuard = createPolicyGuard({
    allowlist: new Set(projection.tool_allowlist),
    secrets,
    providerKind: projection.provider?.kind ?? undefined,
    localRequired: projection.context_blocks?.some(
      (block) => block.sensitivity === "high" || block.sensitivity === "local_required",
    ),
  });

  const guardedStreamSimple = (
    m: Model<Api>,
    context: Context,
    options?: SimpleStreamOptions,
  ) => {
    const guardedOptions = {
      ...options,
      // 中转型上游（如 guga）在重载荷下会间歇 503 service_busy：
      // 网关层按指数退避重试（5xx/408/409/429），不改变请求内容。
      maxRetries: config.providerStreamMaxRetries,
      maxRetryDelayMs: config.providerStreamMaxRetryDelayMs,
      onPayload: async (payload: unknown, _payloadModel: Model<Api>) => {
        // Redaction happens here directly; do NOT re-dispatch through the
        // coding-agent runner's before_provider_request hook, which stringifies
        // the body and corrupts integer token caps on openai-compatible relays.
        return coerceTokenCaps(policyGuard.beforeProviderRequest(payload));
      },
    } satisfies SimpleStreamOptions;
    return deps.streamOverride
      ? deps.streamOverride(m as Model<"openai-completions">, context, guardedOptions)
      : openAIStreamSimple(m as Model<"openai-completions">, context, guardedOptions);
  };

  const registerProviderExtension: InlineExtension = {
    name: "familygraph-provider-registration",
    hidden: true,
    factory: (pi) => {
      pi.registerProvider(providerId, {
        name: `familygraph-${providerId}`,
        baseUrl: entry.baseUrl,
        apiKey: entry.apiKey ?? "unconfigured",
        api: "openai-completions",
        models: [model],
        streamSimple: guardedStreamSimple,
      });
    },
  };

  const loader = new DefaultResourceLoader({
    cwd: agentDir,
    agentDir,
    extensionFactories: [policyGuard.extension, registerProviderExtension],
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    // System prompt is a sidecar-local constant (V2.2 assistant domain prompt);
    // it never travels through the FastAPI context projection.
    systemPrompt: ASSISTANT_SYSTEM_PROMPT,
  });
  await loader.reload();

  const domainTools = createDomainTools((toolName: DomainToolName, call) => {
    if (deps.shouldStopToolCalls?.()) {
      // Cancel requested / lease lost: no further tool calls reach FastAPI.
      throw new Error("run stop requested; tool call skipped");
    }
    return client.executeTool(projection.run_id, runToken, toolName, call);
  });

  const { session } = await createAgentSession({
    cwd: agentDir,
    agentDir,
    modelRuntime,
    model,
    noTools: "all",
    tools: [...projection.tool_allowlist],
    customTools: domainTools,
    resourceLoader: loader,
    sessionManager: SessionManager.inMemory(agentDir),
    settingsManager: SettingsManager.inMemory(),
  });

  return { session, events: new RunEventBuffer(), policyGuard };
}
