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
  current_user_once: false,
  history_in_manager: false,
  lost_response_injected: false,
  retry_duplicate_verified: false,
};
const loseResponse = process.env.FG_SMOKE_LOSE_RESPONSE === "1";
let lostSeq;
let liveSession;
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
  if (String(input).endsWith("/events/append") && response.ok) {
    const body = JSON.parse(String(init?.body ?? "{}"));
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
    const source = projection.context_blocks?.[0];
    assert.ok(source && source.content.includes("清淡"));
    const bundle = await buildRunSession(cfg, cl, projection, runToken, {
      ...deps, agentDir,
      streamOverride: (model, context, options) => {
        facts.provider_calls += 1;
        const texts = context.messages.map(textOf);
        facts.context_source_in_prompt = texts.some((text) => text.includes(source.content) && text.includes(source.citation));
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
            stream.push({ type: "start", partial: response });
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
  for (const key of ["context_build_present", "context_reused", "context_source_in_prompt", "current_user_once", "history_in_manager"]) assert.equal(facts[key], true, key);
  assert.equal(facts.unauthorized_network_attempts, 0);
  if (loseResponse) {
    assert.equal(facts.lost_response_injected, true);
    assert.equal(facts.retry_duplicate_verified, true);
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
