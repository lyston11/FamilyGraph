import { describe, expect, it } from "vitest";

import type { AgentConfig } from "../src/config.js";
import { ProviderPolicyError, resolveProvider } from "../src/session.js";

function makeConfig(internalBase: string): AgentConfig {
  return {
    apiBaseUrl: "http://api:8000",
    internalApiBaseUrl: internalBase,
    serviceSecret: "unit-secret",
    sidecarId: "sc-unit",
    healthPort: 0,
    leasePollIntervalMs: 50,
    defaultLeaseMs: 60_000,
    eventFlushIntervalMs: 10,
    eventFlushBatchSize: 10,
    requestTimeoutMs: 2_000,
    retryBaseDelayMs: 1,
    retryMaxDelayMs: 2,
    retryMaxAttempts: 1,
    providers: {
      cloud: { kind: "openai_compatible", baseUrl: undefined, apiKey: undefined, model: undefined },
      local: { kind: "local", baseUrl: undefined, apiKey: undefined, model: undefined },
    },
  } as unknown as AgentConfig;
}

const ALLOWED = {
  provider_id: "7",
  model: "model-x",
  kind: "openai_compatible" as const,
  policy_result: "allowed" as const,
  secret_ref: "agent_providers/7/secret",
};

describe("resolveProvider（P1 唯一 egress 代理路径）", () => {
  it("代理路径 resolve 到 internal listener，Bearer 用 run token", () => {
    const config = makeConfig("http://api:8001");
    const { entry } = resolveProvider(
      config,
      { ...ALLOWED, base_url: `/internal/agent/runs/42/provider`, api_key: null },
      "run-token-abc",
    );
    expect(entry.baseUrl).toBe("http://api:8001/internal/agent/runs/42/provider");
    expect(entry.apiKey).toBe("run-token-abc");
  });

  it("代理路径带尾部斜杠时不产生双斜杠", () => {
    const config = makeConfig("http://api:8001/");
    const { entry } = resolveProvider(
      config,
      { ...ALLOWED, base_url: "/internal/agent/runs/42/provider", api_key: null },
      "tok",
    );
    expect(entry.baseUrl).toBe("http://api:8001/internal/agent/runs/42/provider");
  });

  it("非代理 base_url（本地 Provider）沿用 projection，api_key 来自下发", () => {
    const config = makeConfig("http://api:8001");
    const { entry } = resolveProvider(
      config,
      { ...ALLOWED, kind: "local", base_url: "http://localhost:11434/v1", api_key: "local-key" },
      "tok",
    );
    expect(entry.baseUrl).toBe("http://localhost:11434/v1");
    expect(entry.apiKey).toBe("local-key");
  });

  it("policy 非 allowed 一律 PROVIDER_UNRESOLVED", () => {
    const config = makeConfig("http://api:8001");
    expect(() =>
      resolveProvider(
        config,
        { ...ALLOWED, policy_result: "denied", base_url: null, api_key: null },
        "tok",
      ),
    ).toThrow(ProviderPolicyError);
  });
});
