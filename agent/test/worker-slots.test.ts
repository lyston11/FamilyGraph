/**
 * Multi-slot isolation regressions (09-25 S1, design §7.4).
 *
 * Why this file exists: the sidecar went from strictly serial (`active: ActiveRun
 * | null`) to a slot map so a 30s-class steward call cannot block a user's
 * question. That is the whole reason the "existing sidecar, multiple slots"
 * option was chosen over a second container — and it is also the largest
 * regression surface in the migration, because it touches the assistant hot path.
 *
 * Each test below pins one property that the serial version got for free and the
 * concurrent version must earn:
 *
 *   1. two slots really run concurrently, and their per-run state does not cross
 *   2. slot budgets are per kind (the value proof for the chosen option)
 *   3. one slot's lease loss / cancellation does not touch another slot
 *   4. a failing lease never leaks a slot
 *
 * The seam is `client.leaseJob` plus a stub session factory, so these exercise
 * the real `SidecarWorker` slot bookkeeping without the Pi SDK.
 */
import { describe, expect, it, vi } from "vitest";
import type { AgentKind, AgentConfig } from "../src/config.js";
import { SidecarWorker } from "../src/worker.js";
import { resolveProvider } from "../src/session.js";
import { createLogger } from "../src/logger.js";
import { makeAgentConfig } from "./helpers.js";

function leasedJob(kind: AgentKind, runId: string) {
  return {
    job_id: `job-${runId}`,
    run_id: runId,
    agent_kind: kind,
    attempt: 1,
    tool_allowlist: [],
    policy_version: "pv",
    run_token: `tok-${runId}`,
    ...(kind === "steward"
      ? { steward_job_id: "1", steward_attempt_id: "2", assist_kind: "terminology", max_concurrent: 1 }
      : {}),
  };
}

interface HarnessOptions {
  overrides?: Partial<AgentConfig>;
  /** Resolve when the test wants a run to finish. */
  jobs?: Record<string, ReturnType<typeof leasedJob> | null>;
}

/**
 * Build a worker whose runs block until released, so several can be in flight
 * at once and their interaction is observable.
 */
function makeWorker(options: HarnessOptions = {}) {
  const config: AgentConfig = {
    ...makeAgentConfig(0),
    role: "both",
    maxConcurrentRuns: 2,
    stewardMaxConcurrentCallsPerSpace: 1,
    ...options.overrides,
  };
  const leaseCalls: AgentKind[] = [];
  const released: Array<() => void> = [];
  const startedRuns: string[] = [];
  const jobs = options.jobs ?? {};

  const client = {
    leaseJob: async (kind: AgentKind = "assistant") => {
      leaseCalls.push(kind);
      const next = jobs[kind];
      if (next === null) return null;
      if (next === undefined) return null;
      return next;
    },
  };

  const worker = new SidecarWorker({
    client: client as never,
    config,
    logger: createLogger(),
  });

  // Stub the run body: the slot bookkeeping is under test, not the Pi loop.
  // `executeJob` is awaited by the slot machinery, so a promise the test
  // controls makes "in flight" a precise state rather than a timing guess.
  vi.spyOn(
    worker as unknown as { executeJob: (job: unknown) => Promise<void> },
    "executeJob",
  ).mockImplementation(async (job: unknown) => {
    startedRuns.push((job as { run_id: string }).run_id);
    await new Promise<void>((resolve) => released.push(resolve));
  });

  return {
    worker,
    leaseCalls,
    startedRuns,
    /** Let every in-flight run finish, releasing its slot. */
    release: () => {
      released.splice(0).forEach((fn) => fn());
    },
  };
}

async function waitFor(predicate: () => boolean, timeoutMs = 1_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error("condition not reached");
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

describe("sidecar slot isolation", () => {
  it("runs two assistant slots concurrently without crossing state", async () => {
    // The queue hands out a different job per call, as the real endpoint does
    // (SQLite's write lock serialises the leases but they are distinct jobs).
    let seq = 0;
    const { worker, startedRuns, release } = makeWorker({
      jobs: {
        get assistant() {
          seq += 1;
          return leasedJob("assistant", `run-${seq}`);
        },
      } as never,
    });

    await worker.leaseIntoSlot("assistant");
    await worker.leaseIntoSlot("assistant");

    // Both slots occupied at once: the property the serial worker could not have.
    expect(worker.inFlight("assistant")).toBe(2);
    await waitFor(() => startedRuns.length === 2);
    expect(new Set(startedRuns).size).toBe(2);

    release();
    await waitFor(() => worker.inFlight("assistant") === 0);
    expect(worker.inFlight("assistant")).toBe(0);
  });

  it("does not let a full steward budget consume assistant slots", async () => {
    // The value proof for the multi-slot option: a long steward call must not
    // stop the assistant from serving a user.
    const jobs: Record<string, unknown> = {
      steward: leasedJob("steward", "steward-1"),
    };
    let assistantSeq = 0;
    const { worker, startedRuns, release } = makeWorker({
      overrides: { maxConcurrentRuns: 2, stewardMaxConcurrentCallsPerSpace: 1 },
      jobs: jobs as never,
    });
    // Fill the single steward slot and keep it occupied.
    jobs["assistant"] = null;

    await worker.leaseIntoSlot("steward");
    expect(worker.inFlight("steward")).toBe(1);
    // Steward budget is exhausted, so another steward lease is refused...
    expect(await worker.leaseIntoSlot("steward")).toBeNull();

    // ...while the assistant budget is untouched and still usable.
    assistantSeq += 1;
    jobs["assistant"] = leasedJob("assistant", `assistant-${assistantSeq}`);
    const assistantRun = await worker.leaseIntoSlot("assistant");
    expect(assistantRun).not.toBeNull();
    expect(worker.inFlight("assistant")).toBe(1);
    expect(worker.inFlight("steward")).toBe(1);
    await waitFor(() => startedRuns.includes("assistant-1"));

    release();
  });

  it("keeps a cancelled slot from disturbing another slot", async () => {
    // Locks design §7.2.1: the cancellation callback carries a run_id, so a
    // single "current run" comparison would silently drop the signal for every
    // other slot.
    let seq = 0;
    const { worker, release } = makeWorker({
      jobs: {
        get assistant() {
          seq += 1;
          return leasedJob("assistant", `run-${seq}`);
        },
      } as never,
    });

    const first = await worker.leaseIntoSlot("assistant");
    const second = await worker.leaseIntoSlot("assistant");
    expect(first).not.toBeNull();
    expect(second).not.toBeNull();
    expect(first!.cancelRequested).toBe(false);
    expect(second!.cancelRequested).toBe(false);

    // Cancel only the first run.
    (worker as unknown as { markCancelRequested: (id: string) => void }).markCancelRequested(
      first!.job.run_id,
    );

    expect(first!.cancelRequested).toBe(true);
    // The second slot must be untouched: its signal was not the one delivered.
    expect(second!.cancelRequested).toBe(false);
    expect(second!.abort.signal.aborted).toBe(false);
    release();
  });

  it("keeps a lease-lost slot from disturbing another slot", async () => {
    let seq = 0;
    const { worker, release } = makeWorker({
      jobs: {
        get assistant() {
          seq += 1;
          return leasedJob("assistant", `run-${seq}`);
        },
      } as never,
    });

    const first = await worker.leaseIntoSlot("assistant");
    const second = await worker.leaseIntoSlot("assistant");

    (worker as unknown as { markLeaseLost: (id: string) => void }).markLeaseLost(
      first!.job.run_id,
    );

    expect(first!.leaseLost).toBe(true);
    expect(second!.leaseLost).toBe(false);
    release();
  });

  it("delivers a cancellation for the *second* slot too (no dropped signal)", async () => {
    // The mirror of the previous test: with a single-run comparison the second
    // slot's cancellation is the one that gets ignored, which is the actual
    // production failure mode.
    let seq = 0;
    const { worker, release } = makeWorker({
      jobs: {
        get assistant() {
          seq += 1;
          return leasedJob("assistant", `run-${seq}`);
        },
      } as never,
    });

    const first = await worker.leaseIntoSlot("assistant");
    const second = await worker.leaseIntoSlot("assistant");

    (worker as unknown as { markCancelRequested: (id: string) => void }).markCancelRequested(
      second!.job.run_id,
    );

    expect(second!.cancelRequested).toBe(true);
    expect(first!.cancelRequested).toBe(false);
    release();
  });

  it("does not leak a slot when the lease call fails repeatedly", async () => {
    // A leaked reservation is the worst failure mode here: concurrency decays to
    // zero and nothing in the logs says why.
    const config: AgentConfig = { ...makeAgentConfig(0), maxConcurrentRuns: 2 };
    let failures = 0;
    const client = {
      leaseJob: async () => {
        failures += 1;
        throw new Error("transient lease failure");
      },
    };
    const worker = new SidecarWorker({ client: client as never, config, logger: createLogger() });

    for (let i = 0; i < 5; i += 1) {
      await expect(worker.leaseIntoSlot("assistant")).rejects.toThrow("transient lease failure");
    }
    expect(failures).toBe(5);
    // Every reservation was released, so the budget is intact...
    expect(worker.inFlight("assistant")).toBe(0);
    expect(worker.isBusy).toBe(false);

    // ...and a working lease still succeeds afterwards.
    (client as { leaseJob: unknown }).leaseJob = async () => leasedJob("assistant", "run-ok");
    const run = await worker.leaseIntoSlot("assistant");
    expect(run).not.toBeNull();
    expect(worker.inFlight("assistant")).toBe(1);
  });

  it("does not count a pending reservation as free while the lease is in flight", async () => {
    // The reservation must be visible to the budget, otherwise two concurrent
    // callers both see a free slot and lease two jobs for one executor.
    const config: AgentConfig = { ...makeAgentConfig(0), maxConcurrentRuns: 1 };
    let resolveLease: ((value: unknown) => void) | undefined;
    const client = {
      leaseJob: () =>
        new Promise((resolve) => {
          resolveLease = resolve;
        }),
    };
    const worker = new SidecarWorker({ client: client as never, config, logger: createLogger() });
    // Keep the leased run in flight so the slot stays occupied.
    vi.spyOn(
      worker as unknown as { executeJob: (job: unknown) => Promise<void> },
      "executeJob",
    ).mockImplementation(() => new Promise<void>(() => {}));

    const pending = worker.leaseIntoSlot("assistant");
    // The lease is suspended at the await; the slot must already read as taken.
    expect(worker.inFlight("assistant")).toBe(1);
    // A second caller is therefore refused rather than oversubscribing.
    expect(await worker.leaseIntoSlot("assistant")).toBeNull();

    resolveLease?.(leasedJob("assistant", "run-1"));
    await expect(pending).resolves.not.toBeNull();
    expect(worker.inFlight("assistant")).toBe(1);
  });

  it("raises the steward budget to the server's broadcast, never lowers it", async () => {
    // Corrects a misconfiguration instead of leaving a leased batch with no
    // executor; only ever raises so the value cannot oscillate.
    const config: AgentConfig = {
      ...makeAgentConfig(0),
      // Steward slots only exist when this instance serves steward jobs, so the
      // role must be set or leaseIntoSlot refuses before reaching the broadcast.
      role: "both",
      stewardMaxConcurrentCallsPerSpace: 1,
    };
    const client = {
      leaseJob: async () => ({
        ...leasedJob("steward", "run-s1"),
        max_concurrent: 3,
      }),
    };
    const worker = new SidecarWorker({ client: client as never, config, logger: createLogger() });
    vi.spyOn(
      worker as unknown as { executeJob: (job: unknown) => Promise<void> },
      "executeJob",
    ).mockImplementation(() => new Promise<void>(() => {}));

    await worker.leaseIntoSlot("steward");
    expect(config.stewardMaxConcurrentCallsPerSpace).toBe(3);

    // A later, lower broadcast must not shrink it back.
    (client as { leaseJob: unknown }).leaseJob = async () => ({
      ...leasedJob("steward", "run-s2"),
      max_concurrent: 1,
    });
    await worker.leaseIntoSlot("steward");
    expect(config.stewardMaxConcurrentCallsPerSpace).toBe(3);
  });
});

describe("tool and prompt isolation between kinds", () => {
  it("gives each kind its disjoint registered tool set", async () => {
    const { toolNamesFor, TOOL_VERSIONS } = await import("../src/tools.js");
    const steward = new Set(toolNamesFor("steward"));
    expect(toolNamesFor("steward")).toEqual([
      "familygraph.steward.get_space_snapshot",
      "familygraph.steward.list_space_nodes",
      "familygraph.steward.get_viewer_target",
      "familygraph.steward.get_viewer_term",
      "familygraph.steward.get_evidence",
      "familygraph.steward.get_relationship_path",
    ]);
    expect(new Set(toolNamesFor("assistant"))).toEqual(
      new Set(Object.keys(TOOL_VERSIONS).filter((name) => !steward.has(name))),
    );
    expect(toolNamesFor("assistant").filter((name) => steward.has(name))).toEqual([]);
  });
});

// ---- run token 续签必须被请求路径按需读取（10-02）----

describe("run token renewal reaches the provider and event paths", () => {
  it("resolves the provider api key from a getter, so renewal is picked up", async () => {
    // 为何是回归：run token 有硬 TTL（600s），心跳会续签它。若 provider 路径在
    // 建 session 时**按值捕获**旧 token，长 run 到期后 provider 请求就会 401——
    // 实测 run 466/467 存活 601/602 秒后正是这样失败的（events/append 与
    // provider/responses 401，而心跳仍 200）。
    let token = "original-token";
    const provider = {
      kind: "openai_compatible" as const,
      base_url: "/internal/agent/runs/1/provider",
      model: "m",
      api: "openai-responses" as const,
      policy_result: "allowed" as const,
      provider_name: "p",
      api_key: null,
      provider_id: "2",
      secret_ref: null,
    };

    const first = resolveProvider(
      { ...makeAgentConfig(0) } as AgentConfig,
      provider,
      () => token,
      "1",
    );
    expect(first.entry.apiKey).toBe("original-token");

    // 模拟心跳续签后重新解析：必须看到新 token，而不是缓存的旧值。
    token = "renewed-token";
    const second = resolveProvider(
      { ...makeAgentConfig(0) } as AgentConfig,
      provider,
      () => token,
      "1",
    );
    expect(second.entry.apiKey).toBe("renewed-token");
  });

  it("keeps the worker's event flusher reading the token at send time", async () => {
    // flusher 在启动时若捕获旧 token，长 run 的 append 会在 TTL 到期后 401。
    // 断言「按需读取」的契约：续签后 flusher 发出的 token 必须变。
    const job = leasedJob("steward", "run-flush");
    const sentTokens: string[] = [];

    const worker = new SidecarWorker({
      client: {
        appendEvents: async (_runId: string, token: string) => {
          sentTokens.push(token);
          return { duplicates: 0 };
        },
      } as never,
      config: { ...makeAgentConfig(0), role: "both" } as AgentConfig,
      logger: createLogger(),
    });

    const flusher = (
      worker as unknown as {
        startEventFlusher: (
          runId: string,
          token: () => string,
          events: { drain(): unknown[] },
          signal?: AbortSignal,
        ) => { flushAll(): Promise<void> };
      }
    ).startEventFlusher(job.run_id, () => job.run_token, {
      drain: () => [{ seq: 1, type: "run.started", payload: {} }],
    });

    job.run_token = "renewed-token";
    await flusher.flushAll();

    expect(sentTokens).toContain("renewed-token");
    expect(sentTokens).not.toContain("tok-run-flush");
  });
});
