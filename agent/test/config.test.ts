import { describe, expect, it } from "vitest";
import { loadConfig, describeProviderReadiness } from "../src/config.js";
import { signServiceToken, verifyServiceToken, peekRunTokenClaims } from "../src/tokens.js";

const BASE_ENV = {
  AGENT_SERVICE_SECRET: "secret-abc",
  FG_API_BASE_URL: "http://api:8000",
  AGENT_PROVIDER_CLOUD_BASE_URL: "https://cloud.example.com/v1",
  AGENT_PROVIDER_CLOUD_API_KEY: "sk-cloud-123",
  AGENT_PROVIDER_CLOUD_MODEL: "gpt-test",
};

describe("loadConfig", () => {
  it("parses env with defaults", () => {
    const config = loadConfig(BASE_ENV as unknown as NodeJS.ProcessEnv);
    expect(config.apiBaseUrl).toBe("http://api:8000");
    expect(config.serviceSecret).toBe("secret-abc");
    expect(config.leasePollIntervalMs).toBe(250);
    expect(config.healthPort).toBe(8080);
    expect(config.providers.cloud.model).toBe("gpt-test");
    expect(config.providers.cloud.apiKey).toBeUndefined();
    expect(config.providers.local.baseUrl).toBeUndefined();
  });

  it("never imports provider API keys from environment", () => {
    const config = loadConfig(BASE_ENV as unknown as NodeJS.ProcessEnv);
    expect(config.providers.cloud.apiKey).toBeUndefined();
  });

  it("rejects missing service secret", () => {
    expect(() => loadConfig({} as NodeJS.ProcessEnv)).toThrow(
      /AGENT_SERVICE_SECRET/,
    );
  });

  it("reports provider readiness for health endpoint", () => {
    const config = loadConfig({
      ...BASE_ENV,
      AGENT_PROVIDER_LOCAL_BASE_URL: "http://localhost:11434/v1",
      AGENT_PROVIDER_LOCAL_MODEL: "llama3",
    } as unknown as NodeJS.ProcessEnv);
    expect(describeProviderReadiness(config)).toEqual({
      cloud: "configured",
      local: "configured",
    });
    const partial = loadConfig(BASE_ENV as unknown as NodeJS.ProcessEnv);
    expect(describeProviderReadiness(partial).local).toBe("missing");
  });
});

describe("service tokens", () => {
  it("round-trips signature verification", () => {
    const token = signServiceToken("s3cret", { sidecarId: "sc1", ttlMs: 60_000 });
    const claims = verifyServiceToken("s3cret", token);
    expect(claims?.typ).toBe("agent_service");
    expect(claims?.sid).toBe("sc1");
  });

  it("fails closed on wrong secret, tampering and expiry", () => {
    const token = signServiceToken("s3cret", { sidecarId: "sc1" });
    expect(verifyServiceToken("other", token)).toBeNull();
    const [header] = token.split(".");
    const forged = `${header}.${Buffer.from(JSON.stringify({ typ: "agent_service", sid: "evil", iat: 0, exp: 9999999999 })).toString("base64url")}.deadbeef`;
    expect(verifyServiceToken("s3cret", forged)).toBeNull();
    const expired = signServiceToken("s3cret", {
      sidecarId: "sc1",
      nowMs: Date.now() - 120_000,
      ttlMs: 60_000,
    });
    expect(verifyServiceToken("s3cret", expired)).toBeNull();
  });

  it("peeks run token claims without verifying (diagnostics only)", () => {
    const payload = Buffer.from(
      JSON.stringify({ run_id: "r1", agent_kind: "assistant", exp: 123 }),
    ).toString("base64url");
    const peek = peekRunTokenClaims(`h.${payload}.sig`);
    expect(peek?.run_id).toBe("r1");
    expect(peekRunTokenClaims("not-a-token")).toBeNull();
  });
});


describe("run retry budget config", () => {
  it("ships a bounded run budget by default", () => {
    // WHY: the request layer and the session layer multiply (6 x 4 = 24 real
    // attempts). Without a run-level ceiling one transient upstream failure
    // occupies a slot for minutes. Measured on the development database: failed
    // runs are p50 = p90 = p99 = 24 egress rows; successful runs are p50 = 3.
    const config = loadConfig(BASE_ENV as unknown as NodeJS.ProcessEnv);
    expect(config.runMaxProviderAttempts).toBe(8);
    expect(config.runMaxTotalRetryMs).toBe(120_000);
  });

  it("lets an operator disable or tighten the budget via env", () => {
    const off = loadConfig({
      ...BASE_ENV,
      AGENT_RUN_MAX_PROVIDER_ATTEMPTS: "0",
      AGENT_RUN_MAX_TOTAL_RETRY_MS: "0",
    } as unknown as NodeJS.ProcessEnv);
    // readInt treats non-positive values as absent, so 0 falls back to the
    // default rather than silently disabling the ceiling.
    expect(off.runMaxProviderAttempts).toBe(8);

    const tight = loadConfig({
      ...BASE_ENV,
      AGENT_RUN_MAX_PROVIDER_ATTEMPTS: "3",
      AGENT_RUN_MAX_TOTAL_RETRY_MS: "30000",
    } as unknown as NodeJS.ProcessEnv);
    expect(tight.runMaxProviderAttempts).toBe(3);
    expect(tight.runMaxTotalRetryMs).toBe(30_000);
  });
});
