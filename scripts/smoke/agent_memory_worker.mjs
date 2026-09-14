// Real SidecarWorker/InternalClient/Pi against isolated FastAPI listeners.
// Only provider streaming and a deliberate lost HTTP response are simulated.
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = process.env.FG_SMOKE_ROOT ?? resolve(fileURLToPath(new URL("../..", import.meta.url)));
const { createAssistantMessageEventStream } = await import(pathToFileURL(resolve(root, "agent/node_modules/@earendil-works/pi-ai/dist/index.js")).href);
const { InternalClient } = await import(pathToFileURL(resolve(root, "agent/dist/client.js")).href);
const { SidecarWorker } = await import(pathToFileURL(resolve(root, "agent/dist/worker.js")).href);
const { buildRunSession } = await import(pathToFileURL(resolve(root, "agent/dist/session.js")).href);
const { loadConfig } = await import(pathToFileURL(resolve(root, "agent/dist/config.js")).href);
const config = loadConfig();
const nativeFetch = globalThis.fetch;
const allowedOrigins = new Set([config.apiBaseUrl, config.internalApiBaseUrl].map((url) => new URL(url).origin));
const facts = {
  provider_calls: 0,
  unauthorized_network_attempts: 0,
  context_build_present: false,
  context_reused: false,
  context_source_in_prompt: false,
  unused_source_in_prompt: false,
  current_user_once: false,
  history_in_manager: false,
  lease_context_attempt_match: false,
  context_reference_on_wire: false,
  context_reference_binding: false,
  context_reference_completed_text_only: false,
  context_reference_private: false,
  original_reference_retry_identical: false,
  lost_response_injected: false,
  retry_duplicate_verified: false,
};
const loseResponse = process.env.FG_SMOKE_LOSE_RESPONSE === "1";
let lostSeq;
let liveSession;
let leaseBinding;
let contextBinding;
let usedSource;
let unusedSource;
let completedText;
const assistantRequests = new Map();
const agentDir = await mkdtemp(resolve(tmpdir(), "fg-memory-smoke-pi-"));
const textOf = (message) => typeof message.content === "string"
  ? message.content
  : (message.content ?? []).filter((block) => block.type === "text").map((block) => block.text).join("");

globalThis.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  if (!allowedOrigins.has(url.origin)) {
    facts.unauthorized_network_attempts += 1;
    throw new Error("synthetic smoke disallows external network");
  }
  return nativeFetch(input, init);
};
const transport = async (input, init) => {
  const response = await globalThis.fetch(input, init);
  const path = new URL(String(input)).pathname;
  if (path.endsWith("/jobs/lease") && response.status === 200) {
    const leased = await response.clone().json();
    leaseBinding = { run_id: leased.run_id, attempt: leased.attempt };
  }
  if (path.endsWith("/context") && response.ok) {
    const context = await response.clone().json();
    contextBinding = { run_id: context.run_id, build_id: context.context_build_id, attempt: context.attempt };
    assert.ok(Number.isInteger(leaseBinding?.attempt) && leaseBinding.attempt > 0);
    assert.equal(contextBinding.run_id, leaseBinding.run_id);
    assert.equal(contextBinding.attempt, leaseBinding.attempt);
    facts.lease_context_attempt_match = true;
  }
  if (path.endsWith("/events/append") && response.ok) {
    assert.equal(path, `/internal/agent/runs/${leaseBinding?.run_id}/events/append`);
    const body = JSON.parse(String(init?.body ?? "{}"));
    for (const entry of body.events ?? []) {
      if (entry.type !== "message.assistant_added") {
        assert.equal(entry.context_reference, undefined);
        continue;
      }
      // Observe the actual HTTP request, not only EventBuffer's local value.
      const reference = entry.context_reference;
      assert.ok(reference);
      assert.deepEqual(Object.keys(reference).sort(), ["attempt", "build_id", "used_handles"]);
      facts.context_reference_on_wire = true;
      assert.equal(reference.build_id, contextBinding?.build_id);
      assert.equal(reference.attempt, leaseBinding?.attempt);
      assert.equal(reference.attempt, contextBinding?.attempt);
      facts.context_reference_binding = true;
      assert.ok(usedSource && unusedSource && completedText);
      assert.equal(entry.public_payload.text, completedText);
      assert.deepEqual(reference.used_handles, [usedSource.citation]);
      assert.ok(!reference.used_handles.includes(unusedSource.citation));
      facts.context_reference_completed_text_only = true;
      for (const key of ["context_reference", "context_build_id", "_source_ref", "used_handles", "citations", "unavailable_citation_count"]) {
        assert.equal(Object.hasOwn(entry.public_payload, key), false);
      }
      facts.context_reference_private = true;
      const original = JSON.stringify(entry);
      if (assistantRequests.has(entry.seq)) {
        assert.equal(original, assistantRequests.get(entry.seq));
        facts.original_reference_retry_identical = true;
      } else {
        assistantRequests.set(entry.seq, original);
      }
    }
    const message = body.events?.find((entry) => entry.type === "message.assistant_added");
    if (message && loseResponse && !facts.lost_response_injected) {
      // FastAPI has durably committed this exact request. Revoke its source
      // before InternalClient retries; idempotency must not rewrite the event.
      lostSeq = message.seq;
      const revoked = await globalThis.fetch(`${config.apiBaseUrl}/api/memories/${process.env.FG_SMOKE_MEMORY_ID}/revoke`, {
        method: "POST",
        headers: { Authorization: `Bearer ${process.env.FG_SMOKE_FAMILY_TOKEN}` },
      });
      assert.equal(revoked.status, 200);
      facts.lost_response_injected = true;
      throw new TypeError("synthetic response lost after durable commit");
    }
    if (facts.lost_response_injected) {
      const accepted = await response.clone().json();
      if (accepted.duplicates?.includes(lostSeq)) facts.retry_duplicate_verified = true;
    }
  }
  return response;
};
const logger = {
  info() {}, warn() {}, error() {}, debug() {},
  child() { return this; },
};
const client = new InternalClient(config, { fetchImpl: transport });
const worker = new SidecarWorker({
  client, config, logger,
  sessionFactory: async (cfg, cl, projection, runToken, deps) => {
    facts.context_build_present = Number.isInteger(projection.context_build_id);
    const repeated = await cl.getRunContext(projection.run_id, runToken, deps?.signal);
    facts.context_reused = repeated.context_build_id === projection.context_build_id
      && JSON.stringify(repeated.context_blocks) === JSON.stringify(projection.context_blocks);
    const current = [...projection.messages].reverse().find((message) => message.role === "user");
    const source = projection.context_blocks?.find((block) => block.source_id === process.env.FG_SMOKE_MEMORY_ID);
    const unused = projection.context_blocks?.find((block) => block.source_id === process.env.FG_SMOKE_UNUSED_MEMORY_ID);
    assert.ok(source && source.content.includes("清淡"));
    assert.ok(unused && unused.citation !== source.citation);
    usedSource = source;
    unusedSource = unused;
    const bundle = await buildRunSession(cfg, cl, projection, runToken, {
      ...deps, agentDir,
      streamOverride: (model, context, options) => {
        facts.provider_calls += 1;
        const texts = context.messages.map(textOf);
        facts.context_source_in_prompt = texts.some((text) => text.includes(source.content) && text.includes(source.citation));
        facts.unused_source_in_prompt = texts.some((text) => text.includes(unused.content) && text.includes(unused.citation));
        facts.current_user_once = texts.filter((text) => text.startsWith(current.content_json.text)).length === 1;
        const stream = createAssistantMessageEventStream();
        void (async () => {
          const response = {
            role: "assistant",
            content: [{ type: "text", text: facts.context_source_in_prompt
              ? `外婆喜欢清淡饮食。[${source.citation}]` : "No source reached the model." }],
            api: model.api, provider: model.provider, model: model.id,
            usage: { input: 100, output: 20, cacheRead: 0, cacheWrite: 0, totalTokens: 120,
              cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
            stopReason: "stop", timestamp: Date.now(),
          };
          try {
            await options?.onPayload?.({ model: model.id, messages: context.messages }, model);
            // A transient partial mentions the other retrieved source. Only
            // the completed assistant text may contribute used_handles.
            const draft = `未完成的草稿。[${unused.citation}]`;
            const partial = { ...response, content: [{ type: "text", text: draft }] };
            stream.push({ type: "start", partial });
            stream.push({ type: "text_delta", contentIndex: 0, delta: draft, partial });
            completedText = response.content[0].text;
            stream.push({ type: "done", reason: "stop", message: response });
            stream.end(response);
          } catch {
            const failed = { ...response, stopReason: "error", errorMessage: "synthetic policy failure" };
            stream.push({ type: "error", reason: "error", error: failed });
            stream.end(failed);
          }
        })();
        return stream;
      },
    });
    liveSession = bundle.session;
    const expected = projection.messages.filter((message) => message.id !== current.id && ["user", "assistant"].includes(message.role));
    const restored = bundle.session.sessionManager.buildSessionContext().messages.map(textOf);
    facts.history_in_manager = expected.every((message) => restored.includes(message.content_json.text));
    return bundle;
  },
});

try {
  assert.equal(await worker.tryLeaseAndRun(), true);
  assert.equal(facts.provider_calls, 1);
  for (const key of ["context_build_present", "context_reused", "context_source_in_prompt", "unused_source_in_prompt", "current_user_once", "history_in_manager", "lease_context_attempt_match", "context_reference_on_wire", "context_reference_binding", "context_reference_completed_text_only", "context_reference_private"]) assert.equal(facts[key], true, key);
  assert.equal(assistantRequests.size, 1);
  assert.equal(facts.unauthorized_network_attempts, 0);
  if (loseResponse) {
    assert.equal(facts.lost_response_injected, true);
    assert.equal(facts.retry_duplicate_verified, true);
    assert.equal(facts.original_reference_retry_identical, true);
  }
  process.stdout.write(JSON.stringify({ verdict: "pass", checks: facts }) + "\n");
} catch (error) {
  process.stdout.write(JSON.stringify({ verdict: "failed", checks: facts, error_type: error?.constructor?.name ?? "Error" }) + "\n");
  process.exitCode = 1;
} finally {
  worker.stop();
  liveSession?.dispose();
  globalThis.fetch = nativeFetch;
  await rm(agentDir, { recursive: true, force: true });
}
