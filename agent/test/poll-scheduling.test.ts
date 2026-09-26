/**
 * LL-R2: the poll loop must not add avoidable latency to the user's wait.
 *
 * Two independent delays existed in `pollLoop`:
 *
 *  1. It slept `leasePollIntervalMs` after *every* iteration, including one
 *     that had just finished a run, so every queued run behind the first waited
 *     a full interval before being noticed.
 *  2. The default interval was 2000ms, so the *first* run also waited on
 *     average half an interval (~1s) before the lease was even attempted.
 *
 * The live baseline showed queue_wait of 0.94s and 11.05s, against a 3s
 * first-segment target — a measurable share of the budget for a pure artifact
 * of the poll schedule.
 *
 * Rewritten for the multi-slot model (09-25 S1). The old assertions counted
 * *total* lease attempts, which no longer means anything: one poll iteration
 * now fills every free slot of every enabled kind. They assert per-kind call
 * sequences instead, and "re-poll immediately" is defined as "a kind with a
 * free slot does not sleep". Slot-budget isolation has its own file
 * (`worker-slots.test.ts`).
 */
import { describe, expect, it } from "vitest";
import type { AgentConfig, AgentKind } from "../src/config.js";
import { loadConfig } from "../src/config.js";
import { SidecarWorker } from "../src/worker.js";
import { createLogger } from "../src/logger.js";
import { makeAgentConfig } from "./helpers.js";

interface Call {
  kind: AgentKind;
  at: number;
}

/**
 * Worker whose *lease call* is stubbed per kind.
 *
 * The seam is `client.leaseJob`, not `tryLeaseAndRun`: `pollLoop` drives
 * `leaseIntoSlot`, so stubbing the outer method would intercept nothing and the
 * tests would silently measure the wrong thing. Going through `leaseJob` also
 * exercises the slot reservation, which is the part the multi-slot model added.
 */
function harness(
  leasesByKind: Partial<Record<AgentKind, boolean[]>>,
  overrides: Partial<AgentConfig> = {},
) {
  const config: AgentConfig = {
    ...makeAgentConfig(0),
    leasePollIntervalMs: 50,
    // Single slot per kind unless a test asks otherwise: with one slot the
    // "does it sleep after work" question is unambiguous.
    maxConcurrentRuns: 1,
    stewardMaxConcurrentCallsPerSpace: 1,
    ...overrides,
  };
  const calls: Call[] = [];
  const started = Date.now();
  const queues: Record<string, boolean[]> = {
    assistant: [...(leasesByKind.assistant ?? [])],
    steward: [...(leasesByKind.steward ?? [])],
  };
  const client = {
    leaseJob: async (kind: AgentKind = "assistant") => {
      calls.push({ kind, at: Date.now() - started });
      const queue = queues[kind] ?? [];
      // An exhausted queue answers null forever, like a real empty queue.
      const leased = queue.length > 0 ? (queue.shift() as boolean) : false;
      if (!leased) return null;
      return {
        job_id: `job-${calls.length}`,
        run_id: `run-${calls.length}`,
        agent_kind: kind,
        attempt: 1,
        tool_allowlist: [],
        policy_version: "pv",
        run_token: "tok",
        ...(kind === "steward" ? { steward_job_id: "1", max_concurrent: 1 } : {}),
      };
    },
  };
  const worker = new SidecarWorker({
    client: client as never,
    config,
    logger: createLogger(),
    // The run lifecycle is not under test here; throwing keeps the loop's
    // scheduling observable without touching the Pi SDK.
    sessionFactory: (() => {
      throw new Error("scheduling test must not build a session");
    }) as never,
  });
  return {
    worker,
    calls,
    failNext: () => {
      const original = client.leaseJob;
      let first = true;
      client.leaseJob = async (kind: AgentKind = "assistant") => {
        if (first) {
          first = false;
          calls.push({ kind, at: Date.now() - started });
          throw new Error("transient lease failure");
        }
        return original(kind);
      };
    },
  };
}

function callsOf(calls: Call[], kind: AgentKind): Call[] {
  return calls.filter((call) => call.kind === kind);
}

/** Let the loop run until `count` lease attempts have happened. */
async function runUntil(calls: Call[], count: number, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (calls.length < count) {
    if (Date.now() > deadline) throw new Error(`only ${calls.length}/${count} attempts`);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

describe("sidecar poll loop scheduling", () => {
  it("fills every free slot without sleeping in between", async () => {
    // The multi-slot invariant that replaces the old "re-poll immediately after
    // a completed run" check. A completed run no longer gates the next attempt:
    // a *free slot* does. With two slots and a non-empty queue, one poll
    // iteration must issue both leases back to back — sleeping between them
    // would add a full interval of latency to the second run.
    const { worker, calls } = harness(
      { assistant: [true, true, false] },
      { maxConcurrentRuns: 2 },
    );
    worker.start();
    await runUntil(calls, 2);
    worker.stop();

    expect(calls[0]!.kind).toBe("assistant");
    expect(calls[1]!.kind).toBe("assistant");
    expect(calls[1]!.at - calls[0]!.at).toBeLessThan(50);
  });

  it("does not lease beyond the slot budget", async () => {
    // One slot, an endlessly non-empty queue: the loop must stop at the budget
    // rather than hoarding leases it has no executor for.
    const { worker, calls } = harness(
      { assistant: [true, true, true, true] },
      { maxConcurrentRuns: 1 },
    );
    worker.start();
    await runUntil(calls, 1);
    // Give the loop time to overshoot if it were going to.
    await new Promise((resolve) => setTimeout(resolve, 40));
    worker.stop();

    // At most one extra attempt can appear: the one issued after the first run
    // released its slot. Four would mean the budget was ignored.
    expect(calls.length).toBeLessThanOrEqual(2);
  });

  it("waits the configured interval when the queue is empty", async () => {
    const { worker, calls } = harness({ assistant: [false, false, false] });
    worker.start();
    await runUntil(calls, 3);
    worker.stop();

    // Empty polls are spaced by the interval (allowing scheduler slack).
    expect(calls[1]!.at - calls[0]!.at).toBeGreaterThanOrEqual(40);
    expect(calls[2]!.at - calls[1]!.at).toBeGreaterThanOrEqual(40);
  });

  it("keeps polling after a failed lease attempt", async () => {
    const { worker, calls, failNext } = harness({ assistant: [true] });
    failNext();
    worker.start();
    await runUntil(calls, 2);
    worker.stop();
    expect(calls.length).toBeGreaterThanOrEqual(2);
  });

  it("polls every enabled kind, each with its own slot budget", async () => {
    // role=both: one poll iteration must attempt both kinds. Two assistant slots
    // and one steward slot, so the first iteration asks assistant twice and
    // steward once.
    const { worker, calls } = harness(
      { assistant: [true, true], steward: [true] },
      { role: "both", maxConcurrentRuns: 2, stewardMaxConcurrentCallsPerSpace: 1 },
    );
    worker.start();
    await runUntil(calls, 3);
    worker.stop();

    expect(callsOf(calls, "assistant").length).toBeGreaterThanOrEqual(2);
    expect(callsOf(calls, "steward").length).toBeGreaterThanOrEqual(1);
  });

  it("never leases a kind this instance does not serve", async () => {
    // role=assistant must not touch the steward queue: the endpoint would 503,
    // but polling it at all would be a wasted round trip every interval.
    const { worker, calls } = harness(
      { assistant: [true], steward: [true] },
      { role: "assistant" },
    );
    worker.start();
    await runUntil(calls, 1);
    worker.stop();

    expect(callsOf(calls, "steward")).toHaveLength(0);
  });

  it("defaults the poll interval low enough to matter for the first run", () => {
    // A queued run waits on average half the interval before the sidecar
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
