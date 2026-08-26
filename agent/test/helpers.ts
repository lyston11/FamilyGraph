import type { AgentConfig } from "../src/config.js";

/** Shared test config factory. */
export function makeAgentConfig(apiPort: number): AgentConfig {
  return {
    apiBaseUrl: `http://127.0.0.1:${apiPort}`,
    serviceSecret: "test-service-secret",
    sidecarId: "sc-test",
    healthPort: 0,
    leasePollIntervalMs: 50,
    defaultLeaseMs: 60_000,
    eventFlushIntervalMs: 20,
    eventFlushBatchSize: 8,
    retryMaxAttempts: 2,
    retryBaseDelayMs: 1,
    retryMaxDelayMs: 2,
    requestTimeoutMs: 2000,
    providers: {
      cloud: {
        kind: "openai_compatible",
        baseUrl: "https://cloud.example.internal/v1",
        apiKey: "sk-cloud-key",
        model: "test-model",
      },
      local: {
        kind: "local",
        baseUrl: "http://127.0.0.1:11434/v1",
        apiKey: undefined,
        model: "llama-test",
      },
    },
  };
}
