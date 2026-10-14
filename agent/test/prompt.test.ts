/** Behavioral-clause assertions for the V2.2 assistant system prompt. */

import { describe, expect, it } from "vitest";
import { buildAssistantSystemPrompt, KINSHIP_PROMPT_TOOLS } from "../src/prompt.js";
import { TOOL_VERSIONS, toolNamesFor } from "../src/tools.js";

/** The prompt for a run whose allowlist carries every assistant tool.
 *
 * The prompt is a function of the run allowlist (kinship sections are dropped when
 * the run may not call the kinship tools), so the behavioural clauses below are
 * asserted against the full-toolset form — the only form in which those sections
 * are sent at all. */
const ASSISTANT_SYSTEM_PROMPT = buildAssistantSystemPrompt(toolNamesFor("assistant"));

describe("assistant system prompt", () => {
  it("declares the single-space read-only family tree assistant identity", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("单空间");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("只读");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("家谱");
  });

  it("enforces the three-state fact discipline with path citations", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("确认事实");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("派生路径");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("逐跳");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("资料不足");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不确定");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("严禁编造");
  });

  it("grounds answers in the structured tree via tool calls, not memory", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("唯一真源");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("调用只读工具");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("记忆");
  });

  it("refuses write requests and denies any confirm-then-write flow", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("拒绝");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("写入、修改或删除");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("确认后写入");
  });

  it("withholds internal instructions, schemas and hidden-field existence", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("内部指令");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("schema");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("可见性规则");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("遮蔽字段");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("作为管理员");
  });
});

describe("kinship term behavior clauses (V2.3)", () => {
  it("routes who-is-this questions through the resolver and path tools", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("resolve_free_text_relation");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("get_relationship_path");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("get_term_alternatives");
  });

  it("relays the clarifying question verbatim under ambiguity, at most one follow-up", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("clarifying_question");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("原样转问用户");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("最多追问一次");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不得自行编造结论");
  });

  it("redirects corrections to personal preference or consented usage recording, never structure", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("record_term_usage");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("明确同意");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("个人显示偏好");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("不得声称能够修改人物之间的结构关系");
  });

  it("carves record_term_usage out as the only write exception in the read-only boundary", () => {
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("唯一例外是称谓用词积累");
    expect(ASSISTANT_SYSTEM_PROMPT).toContain("写入、修改或删除任何结构数据");
  });

  it("names only tools that exist in the registry", () => {
    // The prompt tells the model which tools to call by their canonical dotted
    // name, and ``TOOL_VERSIONS`` in tools.ts is the separate list of what this
    // sidecar may actually register. Nothing else ties the two together: rename a
    // tool on one side only and the model is instructed to call something that
    // does not exist, which fails silently — the run still succeeds, it just
    // stops consulting the structured tree. Asserting membership (rather than
    // comparing the two sets) is deliberate: the prompt names a subset of the
    // registry, and the subset is a behavioural choice.
    const named = new Set(ASSISTANT_SYSTEM_PROMPT.match(/familygraph\.[a-z_]+/g) ?? []);
    expect(named.size).toBeGreaterThan(0);
    expect([...named].filter((name) => !(name in TOOL_VERSIONS))).toEqual([]);
  });
});

describe("kinship sections follow the run allowlist (2026-10-14)", () => {
  const gatedOut = (): string[] => {
    const kinship = new Set<string>(KINSHIP_PROMPT_TOOLS);
    return toolNamesFor("assistant").filter((name) => !kinship.has(name));
  };

  it("drops the kinship sections when the run may not call the kinship tools", () => {
    // Why this is a regression rather than a nicety: the backend refuses all three
    // tools with 503 KINSHIP_FLAG_DISABLED when RELATIONSHIP_INTELLIGENCE_ENABLED
    // is off (and omits them from the allowlist). A prompt that still orders the
    // model to call them burns a model turn plus ~6 sidecar retries per call
    // (503 is retryable) and ends in "the query service is unavailable" —
    // measured on run 1844.
    const prompt = buildAssistantSystemPrompt(gatedOut());
    for (const name of KINSHIP_PROMPT_TOOLS) {
      expect(prompt).not.toContain(name);
    }
    expect(prompt).not.toContain("【称谓与关系问答】");
    expect(prompt).not.toContain("唯一例外是称谓用词积累");
  });

  it("keeps every non-kinship section in place when the kinship sections are dropped", () => {
    const prompt = buildAssistantSystemPrompt(gatedOut());
    expect(prompt).toContain("【事实三态纪律】");
    expect(prompt).toContain("【唯一真源】");
    expect(prompt).toContain("【只读边界】");
    expect(prompt).toContain("【保密与对抗】");
    expect(prompt).toContain("【回答方式】");
    // 【称谓与关系问答】 is the only section that names tools by name, so with the
    // gate off the model learns its tools from the SDK's own declarations (the run
    // allowlist) rather than from the prompt — which is the point: the prompt must
    // not name a tool the run cannot call.
    expect(prompt).not.toMatch(/familygraph\.[a-z_]+/);
    // Sections keep their order and the read-only boundary keeps its closing
    // sentence (the exception clause is the same paragraph, not a new one).
    expect(prompt).toMatch(/不要承诺之后会代为执行。\n\n【保密与对抗】/);
  });

  it("adds the exception clause to the read-only boundary only when enabled", () => {
    const enabled = ASSISTANT_SYSTEM_PROMPT;
    expect(enabled).toContain("不要承诺之后会代为执行。唯一例外是称谓用词积累");
    expect(buildAssistantSystemPrompt(gatedOut())).not.toContain("唯一例外");
  });
});
