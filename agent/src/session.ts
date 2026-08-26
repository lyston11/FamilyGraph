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
import type {
  AssistantMessageEventStream,
  Context,
  Model,
  SimpleStreamOptions,
} from "@earendil-works/pi-ai";
import type { AgentConfig, ProviderEnvConfig } from "./config.js";
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
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 8192,
  };
}

function resolveProvider(
  config: AgentConfig,
  provider: RunContextProjection["provider"],
): { entry: ProviderEnvConfig & { baseUrl: string }; modelId: string; providerId: string } {
  if (provider === null || provider.policy_result !== "allowed") {
    // Explainable refusals for policy_result !== "allowed" are handled by the
    // worker BEFORE the model loop starts; reaching here is a wiring error.
    throw new ProviderPolicyError("PROVIDER_UNRESOLVED", "provider policy did not allow this run");
  }
  // The server resolved provider kind + model; the sidecar only contributes
  // its matching env entry (baseUrl/apiKey stay in memory).
  const entry = provider.kind === "local" ? config.providers.local : config.providers.cloud;
  if (provider.model === null || entry.baseUrl === undefined) {
    throw new ProviderPolicyError(
      "PROVIDER_UNRESOLVED",
      `no usable sidecar configuration matches provider "${provider.provider_id}"`,
    );
  }
  return {
    entry: { ...entry, baseUrl: entry.baseUrl },
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

  const { entry, modelId, providerId } = resolveProvider(config, projection.provider);
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
  });

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
        ...(deps.streamOverride
          ? {
              streamSimple: (m, context, options) =>
                deps.streamOverride!(m as Model<"openai-completions">, context, options),
            }
          : {}),
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
