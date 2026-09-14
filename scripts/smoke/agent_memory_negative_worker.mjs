// Actual leased internal requests and public readers; no Provider or DB access.
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = process.env.FG_SMOKE_ROOT ?? resolve(fileURLToPath(new URL("../..", import.meta.url)));
const { InternalClient } = await import(pathToFileURL(resolve(root, "agent/dist/client.js")).href);
const { loadConfig } = await import(pathToFileURL(resolve(root, "agent/dist/config.js")).href);
const config = loadConfig();
const nativeFetch = globalThis.fetch;
const allowedOrigins = new Set([config.apiBaseUrl, config.internalApiBaseUrl].map((url) => new URL(url).origin));
const checks = { unauthorized_network_attempts: 0 };
const codes = {};
const forgedMarkers = ["smoke-not-a-source", "rag:smoke-forged-handle", "smoke-only-unverified-excerpt"];
const forgedCount = 314159;
const privateKeys = new Set(["context_reference", "context_build_id", "build_id", "used_handles", "_source_ref", "source_ref", "quote_sha256", "content_hash"]);
const sourceKeys = new Set(["source_id", "source_type", "citation_handle", "source_revision", "document_id", "chunk_id", "chunk_index", "index_version", "revision", "scope", "sensitivity"]);
let rawContext;

function hasKey(value, keys) {
  if (value === null || typeof value !== "object") return false;
  return Object.entries(value).some(([key, nested]) => keys.has(key) || hasKey(nested, keys));
}

function assertNoPrivate(value) {
  assert.equal(hasKey(value, privateKeys), false);
  const wire = JSON.stringify(value);
  for (const marker of forgedMarkers) assert.equal(wire.includes(marker), false);
}

function assertInternalHistory(messages) {
  assert.ok(Array.isArray(messages));
  for (const message of messages) {
    assertNoPrivate(message);
    assert.equal(hasKey(message, sourceKeys), false);
    assert.deepEqual(message.content_json.citations ?? [], []);
  }
}

globalThis.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  if (!allowedOrigins.has(url.origin)) {
    checks.unauthorized_network_attempts += 1;
    throw new Error("synthetic smoke disallows external network");
  }
  return nativeFetch(input, init);
};

const client = new InternalClient(config, {
  fetchImpl: async (input, init) => {
    const response = await globalThis.fetch(input, init);
    if (new URL(String(input)).pathname.endsWith("/context") && response.ok) {
      // Capture before InternalClient normalizes the response. A parser that
      // discards forbidden fields must not make this assertion pass.
      rawContext = await response.clone().json();
    }
    return response;
  },
});

async function publicRead(path, headers = {}) {
  const response = await globalThis.fetch(`${config.apiBaseUrl}${path}`, {
    headers: { Authorization: `Bearer ${process.env.FG_SMOKE_FAMILY_TOKEN}`, ...headers },
    signal: AbortSignal.timeout(config.requestTimeoutMs),
  });
  assert.equal(response.status, 200);
  return response;
}

async function publicJson(path) {
  return (await publicRead(path)).json();
}

async function readEvents(runId, cursor) {
  const response = await publicRead(`/api/agent/runs/${runId}/events`, cursor === undefined ? {} : { "Last-Event-ID": String(cursor) });
  const wire = await response.text();
  return wire.split("\n").filter((line) => line.startsWith("data: ")).map((line) => JSON.parse(line.slice(6)));
}

try {
  const job = await client.leaseJob();
  assert.ok(job);
  assert.equal(job.run_id, process.env.FG_SMOKE_RUN_ID);
  const context = await client.getRunContext(job.run_id, job.run_token);
  assert.equal(context.attempt, job.attempt);
  assert.ok(Number.isInteger(context.context_build_id) && context.context_build_id > 0);
  checks.third_run_really_leased = true;

  const sessionPath = `/api/agent/sessions/${context.session_id}/messages`;
  const before = await publicJson(sessionPath);
  const priorAssistants = before.filter((message) => message.role === "assistant");
  assert.equal(priorAssistants.length, 2);
  const rawPrior = rawContext.messages.filter((message) => message.role === "assistant");
  assert.equal(rawPrior.length, 2);
  assertInternalHistory(rawContext.messages);
  checks.raw_internal_history_no_revoked_provenance = true;
  for (const prior of priorAssistants) {
    assert.ok(prior.content_json.text.length > 0);
    assert.equal(rawPrior.find((message) => message.id === prior.id)?.content_json.text, prior.content_json.text);
  }
  checks.raw_internal_history_body_retained = true;
  assert.ok(!rawContext.context_blocks.some((source) => source.source_id === process.env.FG_SMOKE_MEMORY_ID));
  const source = context.context_blocks.find((block) => block.source_id === process.env.FG_SMOKE_UNUSED_MEMORY_ID);
  assert.ok(source);
  checks.third_context_excludes_revoked_keeps_legal_source = true;

  let seq = context.next_event_seq;
  const started = await client.appendEvents(job.run_id, job.run_token, [{ seq, type: "run.started", public_payload: {} }]);
  assert.deepEqual(started.accepted, [seq]);
  const cursor = seq++;
  const submissions = [];
  async function submit(name, payload, reference) {
    const entry = { seq, type: "message.assistant_added", public_payload: payload,
      ...(reference === undefined ? {} : { context_reference: reference }) };
    let result;
    try {
      result = await client.appendEvents(job.run_id, job.run_token, [entry]);
      codes[name] = 200;
    } catch (error) {
      codes[name] = error.status;
      assert.ok(Number.isInteger(error.status) && error.status >= 400 && error.status < 500);
      // A rejected request must not reserve its seq or invalidate the real
      // token. The ordinary legacy body is a positive control at the same seq.
      result = await client.appendEvents(job.run_id, job.run_token, [{ seq, type: entry.type,
        public_payload: { role: "assistant", text: payload.text } }]);
    }
    assert.deepEqual(result.accepted, [seq]);
    assert.deepEqual(result.duplicates, []);
    submissions.push({ name, seq, text: payload.text });
    seq += 1;
  }

  const reference = { build_id: context.context_build_id, attempt: job.attempt, used_handles: [source.citation] };
  await submit("forged_public_fields", {
    role: "assistant", text: "合成引用验收占位回答。",
    citations: [{ source_type: "memory", source_id: forgedMarkers[0], scope: "private", sensitivity: "normal",
      revision: 1, citation_handle: forgedMarkers[1], text: forgedMarkers[2], _source_ref: { quote_sha256: forgedMarkers[2] } }],
    unavailable_citation_count: forgedCount, citations_complete: true,
    _source_ref: { quote_sha256: forgedMarkers[2] }, context_reference: reference,
  });
  await submit("unbound_reference", { role: "assistant", text: `未绑定的合成回答。[${source.citation}]` });
  await submit("wrong_build_reference", { role: "assistant", text: `错构建的合成回答。[${source.citation}]` },
    { ...reference, build_id: reference.build_id + 1000000 });
  await submit("wrong_attempt_reference", { role: "assistant", text: `错执行的合成回答。[${source.citation}]` },
    { ...reference, attempt: reference.attempt + 1 });
  await submit("unused_handle_claim", { role: "assistant", text: "未使用来源的合成回答。" }, reference);
  checks.valid_token_negative_submissions_completed = true;

  await client.getRunContext(job.run_id, job.run_token);
  assert.equal(rawContext.context_build_id, context.context_build_id);
  assertInternalHistory(rawContext.messages);
  checks.raw_internal_history_after_forgery_has_no_provenance = true;
  await client.settleRun(job.run_id, job.run_token, "succeeded");
  assert.equal((await publicJson(`/api/agent/runs/${job.run_id}`)).status, "succeeded");
  checks.negative_driver_settled = true;

  const events = await readEvents(job.run_id);
  const answers = events.filter((event) => event.type === "message.assistant_added");
  assert.equal(answers.length, submissions.length);
  for (const event of events) {
    assertNoPrivate(event);
    assert.ok(Buffer.byteLength(JSON.stringify(event.payload), "utf8") <= 16384);
  }
  for (const submission of submissions) {
    const event = answers.find((item) => item.seq === submission.seq);
    assert.ok(event);
    assert.equal(event.payload.text, submission.text);
    assert.deepEqual(event.payload.citations ?? [], []);
    assert.notEqual(event.payload.unavailable_citation_count, forgedCount);
    const fallback = await publicJson(`/api/agent/runs/${job.run_id}/events/${submission.seq}/citations`);
    assertNoPrivate(fallback);
    assert.deepEqual(fallback.citations, []);
    assert.notEqual(fallback.unavailable_citation_count, forgedCount);
    checks[`${submission.name}_not_certified`] = true;
  }
  checks.normal_sse_and_fallback_reject_forged_metadata = true;
  checks.negative_sse_body_and_byte_limit = true;

  const replay = await readEvents(job.run_id, cursor);
  assert.ok(replay.some((event) => event.seq === submissions[0].seq));
  assert.deepEqual(replay, events.filter((event) => event.seq > cursor));
  replay.forEach(assertNoPrivate);
  checks.reconnect_replays_same_safe_projection = true;

  const history = await publicJson(sessionPath);
  const assistants = history.filter((message) => message.role === "assistant");
  assert.equal(assistants.length, priorAssistants.length + submissions.length);
  for (const message of assistants) {
    assertNoPrivate(message);
    assert.deepEqual(message.citations ?? message.content_json.citations ?? [], []);
    assert.notEqual(message.unavailable_citation_count, forgedCount);
  }
  for (const prior of priorAssistants) {
    const current = assistants.find((message) => message.id === prior.id);
    assert.equal(current?.content_json.text, prior.content_json.text);
    assert.equal(current?.unavailable_citation_count, 1);
  }
  for (const submission of submissions) assert.ok(assistants.some((message) => message.content_json.text === submission.text));
  checks.history_rejects_forged_metadata_keeps_body = true;
  assert.equal(checks.unauthorized_network_attempts, 0);
  process.stdout.write(JSON.stringify({ verdict: "pass", checks, codes }) + "\n");
} catch (error) {
  process.stdout.write(JSON.stringify({ verdict: "failed", checks, codes, error_type: error?.constructor?.name ?? "Error" }) + "\n");
  process.exitCode = 1;
} finally {
  globalThis.fetch = nativeFetch;
}
