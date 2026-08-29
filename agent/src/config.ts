/**
 * Sidecar configuration, sourced exclusively from process environment.
 *
 * Secrets (AGENT_SERVICE_SECRET) live only in process memory and must never
 * be logged or embedded in event payloads. Provider runtime config (base_url,
 * decrypted api_key) is NOT read from these env stubs for real inference — it
 * arrives authoritatively from the backend ProviderGateway via the internal
 * context projection. `providers` here is retained only for health-readiness
 * reporting and unit-test seams. The only on-disk artifact is the Pi SDK's
 * ephemeral auth material inside a container-local temp dir (never a mounted
 * volume). No DB drivers, no fs persistence tools, no shell execution and no
 * general-purpose HTTP fetching dependencies are permitted in this service.
 */

export type ProviderKind = "openai_compatible" | "local";

export interface ProviderEnvConfig {
  kind: ProviderKind;
  baseUrl: string | undefined;
  apiKey: string | undefined;
  model: string | undefined;
}

export interface AgentConfig {
  /** FastAPI base URL (public health probe /api/health). */
  apiBaseUrl: string;
  /** Internal agent protocol base URL (separate listener, P1 network isolation). */
  internalApiBaseUrl: string;
  /** Shared secret used to mint short-lived lease tokens. */
  serviceSecret: string;
  /** Stable sidecar instance identifier used for audit only. */
  sidecarId: string;
  healthPort: number;
  /** Model stream retry policy (relay 5xx backoff, forwarded to pi-ai). */
  providerStreamMaxRetries: number;
  providerStreamMaxRetryDelayMs: number;
  /** Idle poll interval when the durable queue returns no job. */
  leasePollIntervalMs: number;
  /** Lease lifetime advertised by FastAPI; heartbeat fires at lease/3. */
  defaultLeaseMs: number;
  /** Event batch flush thresholds. */
  eventFlushIntervalMs: number;
  eventFlushBatchSize: number;
  /** Network retry policy (network/5xx errors only; never retries 4xx). */
  retryMaxAttempts: number;
  retryBaseDelayMs: number;
  retryMaxDelayMs: number;
  /** Outbound HTTP timeout for internal calls. */
  requestTimeoutMs: number;
  providers: {
    cloud: ProviderEnvConfig;
    local: ProviderEnvConfig;
  };
}

function readString(env: NodeJS.ProcessEnv, key: string): string | undefined {
  const value = env[key];
  return value !== undefined && value.trim() !== "" ? value.trim() : undefined;
}

function readInt(
  env: NodeJS.ProcessEnv,
  key: string,
  fallback: number,
): number {
  const raw = readString(env, key);
  if (raw === undefined) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function readProvider(
  env: NodeJS.ProcessEnv,
  prefix: string,
  kind: ProviderKind,
): ProviderEnvConfig {
  return {
    kind,
    baseUrl: readString(env, `AGENT_PROVIDER_${prefix}_BASE_URL`),
    apiKey: readString(env, `AGENT_PROVIDER_${prefix}_API_KEY`),
    model: readString(env, `AGENT_PROVIDER_${prefix}_MODEL`),
  };
}

export class ConfigError extends Error {}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AgentConfig {
  const serviceSecret = readString(env, "AGENT_SERVICE_SECRET");
  if (!serviceSecret) {
    throw new ConfigError("AGENT_SERVICE_SECRET is required");
  }
  return {
    apiBaseUrl: readString(env, "FG_API_BASE_URL") ?? "http://api:8000",
    // P1 网络隔离：internal 协议走独立 listener（部署默认 api:8001）；
    // 未配置时回退 apiBaseUrl（本地单端口开发形态）。
    internalApiBaseUrl:
      readString(env, "FG_INTERNAL_API_BASE_URL") ??
      readString(env, "FG_API_BASE_URL") ??
      "http://api:8000",
    serviceSecret,
    sidecarId:
      readString(env, "AGENT_SIDECAR_ID") ??
      `sidecar-${process.pid.toString(36)}`,
    healthPort: readInt(env, "HEALTH_PORT", 8080),
    leasePollIntervalMs: readInt(env, "AGENT_LEASE_POLL_MS", 2000),
    providerStreamMaxRetries: readInt(env, "AGENT_PROVIDER_STREAM_MAX_RETRIES", 5),
    providerStreamMaxRetryDelayMs: readInt(env, "AGENT_PROVIDER_STREAM_MAX_RETRY_DELAY_MS", 20000),
    defaultLeaseMs: readInt(env, "AGENT_DEFAULT_LEASE_MS", 60_000),
    eventFlushIntervalMs: readInt(env, "AGENT_EVENT_FLUSH_MS", 250),
    eventFlushBatchSize: readInt(env, "AGENT_EVENT_BATCH_SIZE", 20),
    retryMaxAttempts: readInt(env, "AGENT_RETRY_MAX_ATTEMPTS", 4),
    retryBaseDelayMs: readInt(env, "AGENT_RETRY_BASE_DELAY_MS", 200),
    retryMaxDelayMs: readInt(env, "AGENT_RETRY_MAX_DELAY_MS", 5000),
    requestTimeoutMs: readInt(env, "AGENT_REQUEST_TIMEOUT_MS", 15_000),
    providers: {
      cloud: readProvider(env, "CLOUD", "openai_compatible"),
      local: readProvider(env, "LOCAL", "local"),
    },
  };
}

/** Health-report readiness descriptor: configuration presence only — never a real inference probe. */
export type ProviderReadiness =
  | "configured"
  | "missing"
  | "unknown";

export function describeProviderReadiness(
  config: AgentConfig,
): { cloud: ProviderReadiness; local: ProviderReadiness } {
  const describe = (p: ProviderEnvConfig): ProviderReadiness => {
    if (!p.baseUrl || !p.model) return p.apiKey ? "unknown" : "missing";
    return "configured";
  };
  return { cloud: describe(config.providers.cloud), local: describe(config.providers.local) };
}
