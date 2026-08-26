import { describe, expect, it } from "vitest";
import { backoffDelayMs, DEFAULT_BACKOFF } from "../src/backoff.js";

describe("backoffDelayMs", () => {
  const always = (value: number) => (): number => value;

  it("returns null beyond maxAttempts", () => {
    expect(backoffDelayMs(DEFAULT_BACKOFF, DEFAULT_BACKOFF.maxAttempts + 1)).toBeNull();
    expect(backoffDelayMs(DEFAULT_BACKOFF, 0)).toBeNull();
  });

  it("grows exponentially and stays within [half, full) of the cap", () => {
    const policy = { baseDelayMs: 100, maxDelayMs: 10_000, maxAttempts: 6 };
    // random()=1 → upper bound; random()=0.5 → midpoint.
    expect(backoffDelayMs(policy, 1, always(1))).toBe(100);
    expect(backoffDelayMs(policy, 2, always(1))).toBe(200);
    expect(backoffDelayMs(policy, 3, always(1))).toBe(400);
    // jittered value is in [exp/2, exp)
    const v = backoffDelayMs(policy, 3, always(0.5))!;
    expect(v).toBeGreaterThanOrEqual(200);
    expect(v).toBeLessThan(400);
  });

  it("caps at maxDelayMs", () => {
    const policy = { baseDelayMs: 1000, maxDelayMs: 1500, maxAttempts: 5 };
    expect(backoffDelayMs(policy, 4, always(1))).toBeLessThanOrEqual(1500);
    expect(backoffDelayMs(policy, 9, always(1))).toBeNull();
  });
});
