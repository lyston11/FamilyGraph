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
    expect(config.leasePollIntervalMs).toBe(2000);
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
