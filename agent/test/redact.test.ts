import { describe, expect, it } from "vitest";

import { redactErrorText } from "../src/redact.js";

describe("redactErrorText", () => {
  it("redacts URL credentials", () => {
    const out = redactErrorText("failed to fetch https://user:hunter2@api.example.com/v1/chat");
    expect(out).not.toContain("hunter2");
    expect(out).toContain("[REDACTED]");
    expect(out).toContain("api.example.com");
  });

  it("redacts bearer tokens and api keys", () => {
    expect(redactErrorText("auth failed: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxx")).not.toContain(
      "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    );
    expect(redactErrorText("invalid key sk-proj-abcdef1234567890")).not.toContain("sk-proj-abcdef1234567890");
    expect(redactErrorText('bad request api_key="supersecret123"')).not.toContain("supersecret123");
  });

  it("masks control and non-printable bytes", () => {
    const out = redactErrorText("boom\x00\x01binary\u0007junk");
    expect(out).not.toContain("\x00");
    expect(out).toContain("boom");
  });

  it("caps length at 300 chars", () => {
    const out = redactErrorText("x".repeat(5000));
    expect(out.length).toBeLessThanOrEqual(301);
    expect(out.endsWith("…")).toBe(true);
  });

  it("keeps plain error text intact", () => {
    expect(redactErrorText("model overloaded, retry later")).toBe("model overloaded, retry later");
  });
});
