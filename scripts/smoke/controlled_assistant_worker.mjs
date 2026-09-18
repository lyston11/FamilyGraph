// F controlled acceptance: drive the REAL SidecarWorker / InternalClient / Pi
// session against the isolated FastAPI listeners.
//
// Only the provider stream is scripted (no external inference, no model cost).
// Everything else — lease, context projection, session build, tool execution,
// event append, settle — is production code.
//
// Scenario is selected by FG_SCENARIO. The script prints one JSON line:
//   { verdict, scenario, checks, timings, error_type? }
// Timings are captured at the sidecar boundary so the Python driver can compare
// them with the server-side persisted timing_json (F-R3 before/after).
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root =
  process.env.FG_SMOKE_ROOT ??
  resolve(fileURLToPath(new URL("../..", import.meta.url)));
const { createAssistantMessageEventStream } = await import(
  pathToFileURL(resolve(root, "agent/node_modules/@earendil-works/pi-ai/dist/index.js")).href
);
const { InternalClient } = await import(pathToFileURL(resolve(root, "agent/dist/client.js")).href);
const { SidecarWorker } = await import(pathToFileURL(resolve(root, "agent/dist/worker.js")).href);
const { buildRunSession, SESSION_RETRY_BUDGET } = await import(
  pathToFileURL(resolve(root, "agent/dist/session.js")).href
);
const { loadConfig } = await import(pathToFileURL(resolve(root, "agent/dist/config.js")).href);
const { RunEventBuffer } = await import(pathToFileURL(resolve(root, "agent/dist/events.js")).href);

const config = loadConfig();
const scenario = process.env.FG_SCENARIO ?? "A1";
// Gateway mode exercises the REAL provider_proxy path (Pi -> internal proxy ->
// loopback fake upstream) instead of overriding the stream at the sidecar
// boundary. Used by the A5 error-classification and R5 transport cells.
const gatewayMode = process.env.FG_USE_GATEWAY === "1";
// A3: shrink the advertised context window so the SDK's own compaction logic
// runs for real (we never truncate history ourselves).
const smallContext = process.env.FG_SMALL_CONTEXT === "1";
const singleTurnA3 = process.env.FG_A3_SINGLE === "1";
let lastBundle = null;
const nativeFetch = globalThis.fetch;
const allowedOrigins = new Set(
  [config.apiBaseUrl, config.internalApiBaseUrl].map((url) => new URL(url).origin),
);

const checks = {};
const timings = {};
const facts = {
  provider_calls: 0,
  unauthorized_network_attempts: 0,
  events_seen: [],
  deltas: [],
  assistant_payloads: [],
  tool_calls: [],
  compaction_events: [],
  session_event_types: [],
  session_event_times: [],
  settle_attempts: [],
  heartbeat_outcomes: [],
  internal_requests: [],
  assistant_usages: [],
  compaction_decisions: [],
  provider_call_log: [],
  seen_context_windows: [],
  settle_status: null,
  settle_requests: [],
  abort_seen_ms: null,
  stream_ticks_after_abort: 0,
  stream_finished_ms: null,
};
const startedAt = Date.now();
let providerErrorStatus = 0;

globalThis.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  if (!allowedOrigins.has(url.origin)) {
    facts.unauthorized_network_attempts += 1;
    throw new Error("controlled acceptance disallows external network");
  }
  return nativeFetch(input, init);
};

const transport = async (input, init) => {
  const path = new URL(String(input)).pathname;
  const response = await nativeFetch(input, init);
  // Full internal-request ledger: path + status is the decisive evidence for
  // which endpoint refused a post-cancel write.
  if (path.includes("/internal/agent/") || path.includes("/provider/")) {
    const entry = {
      at_ms: Date.now() - startedAt,
      method: String(init?.method ?? "GET"),
      path,
      status: response.status,
    };
    if (response.status === 409) {
      try {
        entry.error_body = (await response.clone().text()).slice(0, 300);
      } catch {}
    }
    facts.internal_requests.push(entry);
  }
  // Record every FINAL settlement request outcome (status only, never tokens).
  if (path.endsWith("/settle")) {
    let body = {};
    try {
      body = JSON.parse(String(init?.body ?? "{}"));
    } catch {}
    facts.settle_attempts.push({
      at_ms: Date.now() - startedAt,
      http_status: response.status,
      body_status: body.status ?? null,
      body_error_code: body.error_code ?? null,
    });
  }
  if (path.endsWith("/heartbeat")) {
    facts.heartbeat_outcomes.push({
      at_ms: Date.now() - startedAt,
      http_status: response.status,
    });
  }
  if (path.endsWith("/events/append") && response.ok) {
    const body = JSON.parse(String(init?.body ?? "{}"));
    for (const entry of body.events ?? []) {
      facts.events_seen.push(entry.type);
      if (entry.type === "assistant.text_delta") {
        facts.deltas.push({
          at: Date.now(),
          delta: entry.public_payload?.delta ?? "",
        });
      }
      if (entry.type === "message.assistant_added") {
        facts.assistant_payloads.push(entry.public_payload?.text ?? "");
      }
      if (entry.type === "tool.execution.started") {
        facts.tool_calls.push(entry.public_payload?.tool_name ?? "?");
      }
    }
  }
  if (path.endsWith("/settle")) {
    // Record the sidecar's settle DECISION (body + timing), which is what
    // proves which worker branch ran after a cancel.
    facts.settle_requests.push({
      at_ms: Date.now() - startedAt,
      body: JSON.parse(String(init?.body ?? "{}")),
    });
    if (response.ok) {
      const settled = await response.clone().json();
      facts.settle_status = settled?.status ?? null;
    }
  }
  if (path.includes("/provider/") && !response.ok) {
    providerErrorStatus = response.status;
  }
  return response;
};

const logger = {
  info() {},
  warn() {},
  error() {},
  debug() {},
  child() {
    return this;
  },
};

// ---- scripted provider streams -------------------------------------------

const ANSWER = ["The blue tin is ", "in the pantry, ", "second shelf."];
const TOOL_ANSWER = ["I checked the shelf. ", "The tin is in the pantry."];

function usage() {
  // A3 drives the SDK's own compaction threshold: a large reported prompt on
  // turn 1 makes the SDK compact before turn 2's prompt.
  const input = smallContext ? 30_000 : 100;
  return {
    input,
    output: 20,
    cacheRead: 0,
    cacheWrite: 0,
    totalTokens: input + 20,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
  };
}

function finalMessage(model, text, stopReason = "stop") {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: usage(),
    stopReason,
    timestamp: Date.now(),
  };
}

const PART_DELAY_MS = Number.parseInt(process.env.FG_PART_DELAY_MS ?? "120", 10);

/** Stream `parts` with a pause between them, then the authoritative message. */
function scriptedStream(model, context, parts, options) {
  facts.provider_calls += 1;
  const stream = createAssistantMessageEventStream();
  const full = parts.join("");
  if (smallContext) {
    // Record what each provider request carried so a compaction summary request
    // is distinguishable from the answering request (F-R1/A3).
    const users = (context?.messages ?? []).filter((m) => m.role === "user");
    const lastUserText = String(users.at(-1)?.content?.[0]?.text ?? "");
    facts.provider_call_log.push({
      call: facts.provider_calls,
      user_turns: users.length,
      last_user: lastUserText.slice(0, 60),
      // The SDK's summarization request is a normal user message carrying the
      // serialized conversation, so detect it by content, not by an option.
      is_summarization:
        lastUserText.includes("<conversation>") ||
        lastUserText.includes("context checkpoint summary"),
    });
  }
  if (options?.signal) {
    options.signal.addEventListener("abort", () => {
      if (facts.abort_seen_ms === null) facts.abort_seen_ms = Date.now() - startedAt;
    });
  }
  void (async () => {
    const response = finalMessage(model, full);
    try {
      await options?.onPayload?.({ model: model.id, messages: context.messages }, model);
      const started = Date.now();
      timings.first_delta_ms = null;
      let partial = { ...response, content: [{ type: "text", text: "" }] };
      stream.push({ type: "start", partial });
      for (const part of parts) {
        await new Promise((r) => setTimeout(r, PART_DELAY_MS));
        if (options?.signal?.aborted) facts.stream_ticks_after_abort += 1;
        partial = { ...response, content: [{ type: "text", text: (partial.content[0].text ?? "") + part }] };
        if (timings.first_delta_ms === null) timings.first_delta_ms = Date.now() - started;
        stream.push({ type: "text_delta", contentIndex: 0, delta: part, partial });
      }
      timings.last_delta_ms = Date.now() - started;
      facts.stream_finished_ms = Date.now() - startedAt;
      stream.push({ type: "done", reason: "stop", message: response });
      stream.end(response);
    } catch {
      const failed = { ...response, stopReason: "error", errorMessage: "scripted failure" };
      stream.push({ type: "error", reason: "error", error: failed });
      stream.end(failed);
    }
  })();
  return stream;
}

/** A stream that requests one allowlisted tool, then answers. */
function toolStream(model, context, options) {
  facts.provider_calls += 1;
  const stream = createAssistantMessageEventStream();
  void (async () => {
    await options?.onPayload?.({ model: model.id, messages: context.messages }, model);
    const callId = `call_${facts.provider_calls}`;
    const toolCall = {
      role: "assistant",
      content: [
        {
          type: "toolCall",
          id: callId,
          name: process.env.FG_SMOKE_TOOL ?? "familygraph.echo",
          arguments: { text: "pantry" },
        },
      ],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: usage(),
      stopReason: "toolUse",
      timestamp: Date.now(),
    };
    stream.push({ type: "start", partial: toolCall });
    stream.push({ type: "tool_call", contentIndex: 0, partial: toolCall });
    stream.push({ type: "done", reason: "toolUse", message: toolCall });
    stream.end(toolCall);
  })();
  return stream;
}

function streamFor(model, context, options) {
  // A2b and A3 both need a two-turn run; A3 additionally drives the SDK's own
  // compaction threshold by reporting a large prompt usage on turn 1.
  const twoTurn = scenario === "A2b" || (scenario === "A3-compaction" && !singleTurnA3);
  if (scenario === "A3-compaction") facts.seen_context_windows.push(model.contextWindow);
  if (twoTurn && facts.provider_calls === 0) {
    return toolStream(model, context, options);
  }
  const parts = twoTurn ? TOOL_ANSWER : ANSWER;
  return scriptedStream(model, context, parts, options);
}

// ---- run -----------------------------------------------------------------

const agentDir = await mkdtemp(resolve(tmpdir(), "fg-controlled-pi-"));
const buffer = new RunEventBuffer(1);
const worker = new SidecarWorker({
  client: new InternalClient(config, { fetchImpl: transport }),
  config,
  logger,
  sessionFactory: async (cfg, cl, projection, runToken, deps) => {
    checks.lease_attempt = projection.attempt;
    checks.lease_status = projection.status;
    if (smallContext && projection.provider) {
      // Drive the SDK's real compaction path: 40k window minus the shipped
      // 16,384 reserve means a ~30k reported prompt crosses the threshold.
      projection = { ...projection, provider: { ...projection.provider, context_window: 40_000 } };
      checks.small_context_window = projection.provider.context_window;
    }
    const bundle = await buildRunSession(cfg, cl, projection, runToken, {
      ...deps,
      agentDir,
      ...(gatewayMode ? {} : { streamOverride: streamFor }),
    });
    if (smallContext) {
      // Diagnostic: record the SDK's own compaction decision inputs/outputs.
      const session = bundle.session;
      const original = session._checkCompaction.bind(session);
      session._checkCompaction = async (message, skipAbortedCheck) => {
        const settings = session.settingsManager.getCompactionSettings();
        const tokens = message?.usage?.totalTokens ?? null;
        const decision = await original(message, skipAbortedCheck);
        let direct = null;
        try {
          const mod = await import(
            pathToFileURL(
              resolve(root, "agent/node_modules/@earendil-works/pi-coding-agent/dist/core/compaction/index.js"),
            ).href
          );
          direct = mod.shouldCompact(tokens ?? 0, session.model?.contextWindow ?? 0, settings);
        } catch {}
        facts.compaction_decisions.push({
          stopReason: message?.stopReason ?? null,
          tokens,
          contextWindow: session.model?.contextWindow ?? null,
          reserveTokens: settings.reserveTokens,
          enabled: settings.enabled,
          direct_should_compact: direct,
          triggered: decision,
        });
        return decision;
      };
    }
    const unsubscribe = bundle.session.subscribe((event) => {
      buffer.onSessionEvent(event);
      const ev = event ?? {};
      const type = typeof ev.type === "string" ? ev.type : "?";
      facts.session_event_types.push(type);
      facts.session_event_times.push({
        type,
        reason: typeof ev.reason === "string" ? ev.reason : null,
        at_ms: Date.now() - startedAt,
      });
      if (type === "message_end" && ev.message?.role === "assistant") {
        facts.assistant_usages.push({
          stopReason: ev.message.stopReason ?? null,
          usage: ev.message.usage ?? null,
        });
      }
      if (type === "compaction_start") {
        facts.compaction_events.push(`compaction_start:${ev.reason ?? "?"}`);
      } else if (type === "compaction_end") {
        facts.compaction_events.push("compaction_end");
      }
    });
    checks.session_subscribed = typeof unsubscribe === "function";
    checks.auto_compaction_enabled = bundle.session.autoCompactionEnabled;
    checks.compaction_settings = bundle.session.settingsManager.getCompactionSettings();
    checks.model_context_window = bundle.session.model?.contextWindow ?? null;
    checks.history_messages = bundle.session.sessionManager.buildSessionContext().messages.length;
    if (smallContext) {
      lastBundle = bundle;
    }
    return bundle;
  },
});

try {
  const leased = await worker.tryLeaseAndRun();
  checks.leased = leased === true;
  if (gatewayMode) {
    // The gateway path is asserted by the Python driver from real upstream
    // request counts and persisted egress audit rows.
    checks.unauthorized_network_attempts = facts.unauthorized_network_attempts;
    checks.settle_status = facts.settle_status;
    checks.provider_error_status = providerErrorStatus;
    checks.session_retry_budget = SESSION_RETRY_BUDGET;
    checks.compaction_events = facts.compaction_events;
    checks.seen_context_windows = facts.seen_context_windows;
    timings.provider_calls = facts.provider_calls;
    process.stdout.write(
      JSON.stringify({ verdict: "pass", scenario, checks, timings }) + "\n",
    );
  } else {
  const drained = buffer.drain();
  const provisional = drained
    .filter((e) => e.type === "assistant.text_delta")
    .map((e) => e.public_payload.delta)
    .join("");
  const authoritative = drained
    .filter((e) => e.type === "message.assistant_added")
    .map((e) => e.public_payload.text);
  checks.provisional_concatenates = provisional === ANSWER.join("");
  checks.authoritative_exactly_once = authoritative.length === 1;
  checks.authoritative_is_full_answer = authoritative[0] === ANSWER.join("");
  checks.delta_precedes_authoritative =
    drained.findIndex((e) => e.type === "assistant.text_delta") <
    drained.findIndex((e) => e.type === "message.assistant_added");
  checks.no_thinking_leaked = !JSON.stringify(drained).includes("thinking");
  checks.unauthorized_network_attempts = facts.unauthorized_network_attempts;
  checks.settle_status = facts.settle_status;
  checks.provider_error_status = providerErrorStatus;
  checks.session_retry_budget = SESSION_RETRY_BUDGET;
  checks.settle_requests = facts.settle_requests;
  checks.compaction_events = facts.compaction_events;
  checks.session_event_types = facts.session_event_types;
  checks.session_event_times = facts.session_event_times;
  // Mirror of the worker's heartbeat cadence (worker.ts startHeartbeat:
  // max(floor(defaultLeaseMs/3), 1000)). Reported so a short run's zero
  // heartbeats can be explained rather than merely observed.
  checks.heartbeat_interval_ms = Math.max(
    Math.floor(config.defaultLeaseMs / 3),
    1000,
  );
  checks.default_lease_ms = config.defaultLeaseMs;
  checks.settle_attempts = facts.settle_attempts;
  checks.heartbeat_outcomes = facts.heartbeat_outcomes;
  checks.internal_requests = facts.internal_requests;
  checks.assistant_usages = facts.assistant_usages;
  checks.compaction_decisions = facts.compaction_decisions;
  checks.provider_call_log = facts.provider_call_log;
  if (lastBundle) {
    // Diagnostic: after the run, does the SDK have summarizable history?
    try {
      const mod = await import(
        pathToFileURL(
          resolve(root, "agent/node_modules/@earendil-works/pi-coding-agent/dist/core/compaction/index.js"),
        ).href
      );
      const branch = lastBundle.session.sessionManager.getBranch();
      const context = lastBundle.session.sessionManager.buildSessionContext();
      const prep = mod.prepareCompaction(
        branch,
        lastBundle.session.settingsManager.getCompactionSettings(),
      );
      checks.branch_entries = branch.length;
      checks.context_messages = context.messages.length;
      checks.estimated_context_tokens = mod.estimateContextTokens(context.messages).tokens;
      checks.prepare_compaction = prep
        ? {
            tokensBefore: prep.tokensBefore,
            summarize: prep.messagesToSummarize.length,
            turnPrefix: prep.turnPrefixMessages.length,
          }
        : null;
    } catch (error) {
      checks.prepare_compaction_error = String(error?.message ?? error).slice(0, 200);
    }
  }
  checks.seen_context_windows = facts.seen_context_windows;
  checks.abort_seen_ms = facts.abort_seen_ms;
  checks.stream_ticks_after_abort = facts.stream_ticks_after_abort;
  checks.stream_finished_ms = facts.stream_finished_ms;
  timings.provider_calls = facts.provider_calls;
  process.stdout.write(
    JSON.stringify({ verdict: "pass", scenario, checks, timings }) + "\n",
  );
  }
} catch (error) {
  process.stdout.write(
    JSON.stringify({
      verdict: "failed",
      scenario,
      checks,
      timings,
      provider_calls: facts.provider_calls,
      error_type: error?.constructor?.name ?? "Error",
      error_message: String(error?.message ?? error).slice(0, 300),
    }) + "\n",
  );
  process.exitCode = 1;
} finally {
  worker.stop();
  globalThis.fetch = nativeFetch;
  await rm(agentDir, { recursive: true, force: true });
}
