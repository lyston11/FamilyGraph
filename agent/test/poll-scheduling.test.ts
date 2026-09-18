/**
 * LL-R2: the poll loop must not add avoidable latency to the user's wait.
 *
 * Two independent delays existed in `pollLoop`:
 *
 *  1. It slept `leasePollIntervalMs` after *every* iteration, including one
 *     that had just finished a run. With the serial sidecar that means every
 *     queued run behind the first waited a full interval before being noticed.
 *  2. The default interval was 2000ms, so the *first* run also waited on
 *     average half an interval (~1s) before the lease was even attempted.
 *
 * The live baseline showed queue_wait of 0.94s and 11.05s, against a 3s
 * first-segment target — a measurable share of the budget for a pure artifact
 * of the poll schedule. These tests pin the loop's shape against the real
 * worker class with a stubbed lease call, so they stay fast and deterministic.
 */
import { describe, expect, it, vi } from "vitest";
import type { AgentConfig } from "../src/config.js";
import { loadConfig } from "../src/config.js";
import { SidecarWorker } from "../src/worker.js";
import { createLogger } from "../src/logger.js";
import { makeAgentConfig } from "./helpers.js";

/**
 * Worker whose `tryLeaseAndRun` is stubbed: the loop's scheduling is the unit
 * under test, not the run lifecycle.
 */
function harness(leases: Array<boolean>) {
  const config: AgentConfig = { ...makeAgentConfig(0), leasePollIntervalMs: 50 };
  const worker = new SidecarWorker({
    client: {} as never,
    config,
    logger: createLogger(),
  });
  const calls: number[] = [];
  let index = 0;
  const started = Date.now();
  vi.spyOn(worker, "tryLeaseAndRun").mockImplementation(async () => {
    calls.push(Date.now() - started);
    const result = leases[index] ?? false;
    index += 1;
    return result;
  });
  return { worker, calls };
}

/** Let the loop run until `count` lease attempts have happened. */
async function runUntil(calls: number[], count: number, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (calls.length < count) {
    if (Date.now() > deadline) throw new Error(`only ${calls.length}/${count} attempts`);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

describe("sidecar poll loop scheduling", () => {
  it("re-polls immediately after a completed run instead of sleeping first", async () => {
    // Three consecutive runs then an empty queue. If the loop slept after a
    // successful lease, attempts 2 and 3 would each be >= interval apart.
    const { worker, calls } = harness([true, true, true, false]);
    worker.start();
    await runUntil(calls, 4);
    worker.stop();

    expect(calls).toHaveLength(4);
    // Attempts 2 and 3 follow a completed run: they must be near-instant, not
    // one full poll interval later.
    expect(calls[1]! - calls[0]!).toBeLessThan(50);
    expect(calls[2]! - calls[1]!).toBeLessThan(50);
    // Attempt 4 also follows a completed run (the empty queue is only learned
    // by asking), so it too is immediate...
    expect(calls[3]! - calls[2]!).toBeLessThan(50);
    // ...and it is the empty result that then imposes the idle interval: no
    // fifth attempt may follow within it.
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(calls).toHaveLength(4);
  });

  it("waits the configured interval when the queue is empty", async () => {
    const { worker, calls } = harness([false, false, false]);
    worker.start();
    await runUntil(calls, 3);
    worker.stop();

    // Empty polls are spaced by the interval (allowing scheduler slack).
    expect(calls[1]! - calls[0]!).toBeGreaterThanOrEqual(40);
    expect(calls[2]! - calls[1]!).toBeGreaterThanOrEqual(40);
  });

  it("keeps polling after a failed lease attempt", async () => {
    const { worker, calls } = harness([false, true]);
    vi.spyOn(worker, "tryLeaseAndRun").mockImplementationOnce(async () => {
      calls.push(0);
      throw new Error("transient lease failure");
    });
    worker.start();
    await runUntil(calls, 2);
    worker.stop();
    expect(calls.length).toBeGreaterThanOrEqual(2);
  });

  it("defaults the poll interval low enough to matter for the first run", () => {
    // A queued run waits on average half the interval before the serial sidecar
    // notices it. 2000ms contributed ~1s of pure queue_wait against the 3s
    // first-segment target, so the shipped default must stay materially below
    // it. Read through the real env parser, not the test config factory.
    const config = loadConfig({
      AGENT_SERVICE_SECRET: "s",
      FG_API_BASE_URL: "http://api:8000",
    } as unknown as NodeJS.ProcessEnv);
    expect(config.leasePollIntervalMs).toBe(250);
  });
});
