import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { RunContextBlock } from "../src/client.js";
import { estimateContext, RAG_ESTIMATOR_VERSION, renderContextAppendix } from "../src/context.js";

const witness = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/rag_context_envelope_v1.json", import.meta.url), "utf8")) as {
  blocks: RunContextBlock[]; appendix: string; estimated_tokens: number; estimator_version: string;
};

describe("RAG appendix contract shared with ContextBuilder", () => {
  it("matches the frozen full envelope including Unicode and citation instructions", () => {
    expect(renderContextAppendix(witness.blocks)).toBe(witness.appendix);
    expect(estimateContext(witness.blocks)).toBe(witness.estimated_tokens);
    expect(RAG_ESTIMATOR_VERSION).toBe(witness.estimator_version);
    expect(renderContextAppendix([])).toBe("");
    expect(estimateContext([])).toBe(0);
  });
});
